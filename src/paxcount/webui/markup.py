"""Разметка дверей эталонного визита: кадр, рамка кузова, проёмы — рукой.

Отдельный экран, а не вкладка в существующем: старый редактор зон работает
поверх кэша треков (`/api/state` требует `paxcount run`), а у боевой записи
кэша нет и не будет — детекция трёх часов видео ради пяти кадров не окупается.
Здесь кадр читается прямо из файла по номеру, и ни детектор, ни трекер не
участвуют вовсе.

Что размечается и почему именно это:

* **рамка кузова** — не украшение. По ней видно, обрезан корпус краем кадра
  или виден целиком, а от этого зависит, законна ли галочка «дверь за кадром»
  (решение 038, та же проверка перенесена на ручную разметку). Она же — вход
  всех автоматических методов: сравнивать их с эталоном можно только на одной
  и той же рамке.
* **проёмы дверей в пикселях кадра** — из них режется кроп для счёта.
* **номер от носа и признак «в кадре»** — из них выводится код 5-8 таблицы 2.

Арифметика по таблице 3 проверяется до записи. Замечание не запрещает
сохранить разметку — человек может знать больше проверки, — но требует
повторного подтверждения: молча записанный эталон с потерянной дверью и есть
та ошибка, ради которой вся проверка написана (борт 7861).
"""

from __future__ import annotations

import io
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import APIRouter, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from .. import cameras
from ..delivery import clocks
from ..delivery.model import VehicleSize
from ..delivery.timeline import parse_slot
from ..settings import DATA_DIR, PROD_VIDEO_DIR, videos_in
from ..truth import (
    DoorLayout,
    DoorLayoutEntry,
    check_door_arithmetic,
    door_layout_path,
    duplicate_visit_problems,
    load_door_layout,
    save_door_layout,
)

router = APIRouter()
PAGE_PATH = Path(__file__).parent / "markup.html"
_PAGE_CACHE: dict[float, str] = {}


def _page() -> str:
    """Страница с диска, перечитанная при изменении файла.

    Читать один раз при импорте оказалось дорого: правка экрана требовала
    перезапуска сервера, и дважды подряд владелец видел старую версию, считая
    правку несделанной. Диск тут не узкое место — файл отдаётся раз на заход.
    """
    stamp = PAGE_PATH.stat().st_mtime
    if stamp not in _PAGE_CACHE:
        _PAGE_CACHE.clear()
        _PAGE_CACHE[stamp] = PAGE_PATH.read_text(encoding="utf-8").replace(
            "{{ВЕРСИЯ}}", datetime.fromtimestamp(stamp).strftime("%d.%m %H:%M:%S"))
    return _PAGE_CACHE[stamp]

DOORS_DIR = DATA_DIR / "truth" / "doors"
CLOCKS_DIR = DATA_DIR / "clocks"
CAMERAS_DIR = DATA_DIR / "cameras"

# Метаданные файла (размер кадра, fps, число кадров) не меняются, а открытие
# сотни видео на каждый запрос списка — секунды ожидания на ровном месте.
_META: dict[str, dict] = {}


def _sources() -> list[Path]:
    """Боевые записи камер, по которым ведётся счёт.

    Отладочный набор не входит: эталон строится по бою. К1 тоже не входит —
    она опознаёт машину, но не считает (решение 030), а разметка дверей нужна
    ровно для нарезки кропа под счёт.
    """
    if not PROD_VIDEO_DIR.is_dir():
        return []
    return [p for p in videos_in(PROD_VIDEO_DIR)
            if cameras.counts(_meta(p)["camera"])]


def _find(stem: str) -> Path:
    for p in _sources():
        if p.stem == stem:
            return p
    raise HTTPException(404, f"нет такого файла среди боевых записей: {stem}")


def _meta(path: Path) -> dict:
    if path.stem in _META:
        return _META[path.stem]
    from ..core.video import probe

    meta = probe(path)
    slot = None
    try:
        slot = parse_slot(path.stem)
    except ValueError:
        pass
    record = {
        "stem": path.stem,
        "name": path.name,
        "width": meta.width,
        "height": meta.height,
        "fps": round(meta.fps, 4),
        "frames": meta.frame_count,
        "duration_s": round(meta.duration_s, 1),
        "camera": slot.camera if slot else "",
        "stop": slot.stop if slot else "",
        "start": slot.start.isoformat() if slot else None,
    }
    _META[path.stem] = record
    return record


def _reference_time(stem: str, camera: str, stop: str, raw: datetime) -> str | None:
    """Момент на шкале К2 — если поправка для ЭТОГО файла замерена.

    Соседний файл той же камеры поправку не одалживает (`clocks._offset_for`),
    поэтому отсутствие записи здесь — честное «неизвестно», а не ноль.
    """
    records = clocks.load(CLOCKS_DIR / f"{stop}.csv")
    try:
        return clocks.to_reference(raw, camera, stem, records).isoformat()
    except LookupError:
        return None


@router.get("/markup", response_class=HTMLResponse)
def markup_page() -> HTMLResponse:
    # no-store, а не просто no-cache: браузер обязан спросить сервер заново.
    # Без этого обычная перезагрузка показывала прежнюю страницу, и правки
    # интерфейса выглядели невнесёнными.
    return HTMLResponse(_page(), headers={"Cache-Control": "no-store, max-age=0"})


@router.get("/api/markup/files")
def api_files() -> JSONResponse:
    # Порядок — камера, затем время старта: оператор отдаёт смену папками
    # «Камера 1/Утро», «Камера 1/Вечер», и в алфавитном обходе вечер стоит
    # перед утром, а камеры чередуются.
    rows = sorted(
        (_meta(p) for p in _sources()),
        key=lambda r: (r["camera"], r["start"] or ""),
    )
    return JSONResponse({
        "files": rows,
        "layouts": _saved_layouts(),
        # Не молчим о том, чего в списке нет: пропавшая камера выглядит
        # поломкой, если не сказать, что она убрана нарочно.
        "excluded": sorted(set(_all_cameras()) - set(cameras.COUNTING_CAMERAS)),
    })


def _all_cameras() -> list[str]:
    if not PROD_VIDEO_DIR.is_dir():
        return []
    return [_meta(p)["camera"] for p in videos_in(PROD_VIDEO_DIR)]


def _stored_layouts() -> list[DoorLayout]:
    """Все записанные разметки. Нужны, чтобы поймать второй файл на один визит."""
    if not DOORS_DIR.is_dir():
        return []
    out = []
    for path in sorted(DOORS_DIR.rglob("*.json")):
        try:
            out.append(load_door_layout(path))
        except ValueError:
            continue
    return out


def _saved_layouts() -> list[dict]:
    """Уже размеченные визиты — чтобы к работе можно было вернуться.

    Без этого списка попасть в сохранённую разметку значит вручную найти тот
    же кадр: ключ визита содержит время с точностью до миллисекунд.
    """
    if not DOORS_DIR.is_dir():
        return []
    rows = []
    for path in sorted(DOORS_DIR.rglob("*.json")):
        try:
            layout = load_door_layout(path)
        except ValueError:
            continue
        if not cameras.counts(layout.camera):
            continue
        rows.append({
            "visit_key": layout.visit_key,
            "camera": layout.camera,
            "video": layout.video,
            "frame_idx": layout.frame_idx,
            "size": layout.size.value,
            "doors": len(layout.doors),
            "in_frame": sum(d.in_frame for d in layout.doors),
        })
    return rows


@router.get("/api/markup/at")
def api_at(stem: str, frame: int) -> JSONResponse:
    """Что за момент показывает этот кадр — по часам камеры и по шкале К2."""
    path = _find(stem)
    meta = _meta(path)
    if not meta["start"]:
        return JSONResponse({"raw": None, "reference": None})
    raw = datetime.fromisoformat(meta["start"]) + timedelta(seconds=frame / meta["fps"])
    return JSONResponse({
        "raw": raw.isoformat(),
        "reference": _reference_time(stem, meta["camera"], meta["stop"], raw),
    })


@router.get("/api/markup/frame")
def api_frame(stem: str, frame: int = 0, quality: int = 92) -> Response:
    import cv2

    path = _find(stem)
    cap = cv2.VideoCapture(str(path))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame)
    ok, image = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(404, f"кадр {frame} не читается")
    ok, buf = cv2.imencode(".jpg", image, [cv2.IMWRITE_JPEG_QUALITY, quality])
    return Response(io.BytesIO(buf.tobytes()).getvalue(), media_type="image/jpeg")


@router.get("/api/markup/layout")
def api_layout_get(camera: str, visit_key: str) -> JSONResponse:
    path = door_layout_path(camera, visit_key, root=DOORS_DIR)
    if not path.exists():
        raise HTTPException(404, "разметки для этого визита ещё нет")
    return JSONResponse(load_door_layout(path).model_dump(mode="json"))


@router.get("/api/markup/motion")
def api_motion_get(stop: str, camera: str) -> JSONResponse:
    """Направление движения в кадре этой камеры — и нос, который из него следует."""
    records = cameras.load(cameras.path_for(stop, CAMERAS_DIR))
    motion = cameras.motion_for(stop, camera, records)
    return JSONResponse({
        "motion": motion,
        "nose": cameras.nose_from_motion(motion) if motion else None,
        "measured_by": next((r.measured_by for r in records
                              if r.stop == stop and r.camera == camera), None),
    })


class MotionPayload(BaseModel):
    stop: str
    camera: str
    motion: str
    measured_by: str = "задано в экране разметки"


@router.post("/api/markup/motion")
def api_motion_set(payload: MotionPayload) -> JSONResponse:
    """Задаёт направление один раз на камеру. Нос после этого не спрашивается."""
    if payload.motion not in cameras.MOTIONS:
        raise HTTPException(422, f"направление должно быть одним из {cameras.MOTIONS}")
    path = cameras.path_for(payload.stop, CAMERAS_DIR)
    records = cameras.load(path)
    records.append(cameras.CameraMotion(
        stop=payload.stop, camera=payload.camera,
        motion=payload.motion, measured_by=payload.measured_by,
    ))
    cameras.save(path, records)
    return JSONResponse({
        "motion": payload.motion,
        "nose": cameras.nose_from_motion(payload.motion),
        "saved": str(path),
    })


class MarkupDoor(BaseModel):
    n_from_nose: int
    opening_px: tuple[float, float, float, float] | None = None
    in_frame: bool


class MarkupPayload(BaseModel):
    visit_key: str
    camera: str
    size: str
    orientation: str
    video: str
    frame_idx: int
    frame_size: tuple[int, int]
    body_px: tuple[float, float, float, float] | None = None
    doors: list[MarkupDoor]
    # Замечание арифметики не запрет, но и не шум: чтобы записать разметку
    # вопреки ему, человек подтверждает это вторым нажатием.
    force: bool = False


@router.post("/api/markup/layout")
def api_layout_save(payload: MarkupPayload) -> JSONResponse:
    try:
        layout = DoorLayout(
            visit_key=payload.visit_key,
            camera=payload.camera,
            size=VehicleSize(payload.size),
            orientation=payload.orientation,
            video=payload.video,
            frame_idx=payload.frame_idx,
            frame_size=payload.frame_size,
            body_px=payload.body_px,
            doors=[DoorLayoutEntry(**d.model_dump()) for d in payload.doors],
        )
    except ValueError as exc:
        raise HTTPException(422, str(exc)) from exc

    problems = check_door_arithmetic(layout)
    problems += duplicate_visit_problems(layout, _stored_layouts())
    if problems and not payload.force:
        return JSONResponse({"saved": None, "problems": problems})

    path = door_layout_path(payload.camera, payload.visit_key, root=DOORS_DIR)
    save_door_layout(path, layout)
    return JSONResponse({"saved": str(path), "problems": problems})


@router.delete("/api/markup/layout")
def api_layout_delete(camera: str, visit_key: str) -> JSONResponse:
    path = door_layout_path(camera, visit_key, root=DOORS_DIR)
    if path.exists():
        path.unlink()
    return JSONResponse({"deleted": str(path)})
