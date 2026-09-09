"""Локальный веб-UI: разметка дверей, запуск, просмотр результата.

Ручная правка зоны двери — главный рычаг точности: автопредложение не знает,
где именно дверь, а человек видит это за секунду.
"""

from __future__ import annotations

import io
from pathlib import Path

import cv2
from fastapi import FastAPI, HTTPException
from fastapi.responses import HTMLResponse, JSONResponse, Response
from pydantic import BaseModel

from ..backends.custom import CustomBackend
from ..core.trackdata import cache_path, load
from ..core.types import DoorSpec, Point, VideoConfig
from ..core.video import probe
from ..doors import load_config, save_config
from ..tracking import get_tracks
from ..settings import VIDEO_SUFFIXES, videos_in
from ..visits import build_scene

app = FastAPI(title="paxcount")
PAGE = (Path(__file__).parent / "index.html").read_text(encoding="utf-8")


def _videos() -> list[Path]:
    return videos_in(None)


def _find(stem: str) -> Path:
    for p in _videos():
        if p.stem == stem:
            return p
    raise HTTPException(404, f"нет такого видео: {stem}")


def _tracks(video: Path):
    path = cache_path(video)
    if not path.exists():
        raise HTTPException(
            409, "нет кэша треков. Выполните: paxcount run <видео> --backend custom"
        )
    return load(path)


@app.get("/", response_class=HTMLResponse)
def index() -> str:
    return PAGE


@app.get("/api/videos")
def api_videos() -> JSONResponse:
    rows = []
    for p in _videos():
        meta = probe(p)
        cached = cache_path(p).exists()
        from ..doors import config_path

        cfg_path = config_path(p)
        rows.append(
            {
                "stem": p.stem, "name": p.name,
                "width": meta.width, "height": meta.height,
                "fps": round(meta.fps, 2), "duration": round(meta.duration_s, 1),
                "cached": cached, "manual": cfg_path.exists(),
            }
        )
    return JSONResponse(rows)


@app.get("/api/state/{stem}")
def api_state(stem: str) -> JSONResponse:
    video = _find(stem)
    data = _tracks(video)
    cfg = load_config(video)
    scene = build_scene(data, cfg)
    visits = scene.visits
    # Кадр для разметки — середина самого длинного визита, и рамки того же
    # визита: самый длинный визит не обязан быть визитом главного ТС сцены.
    if visits:
        longest = max(visits, key=lambda v: v.departure_ts - v.arrival_ts)
        t = (longest.arrival_ts + longest.departure_ts) / 2
        boxes = scene.boxes_for(longest)
    else:
        t = data.duration_s / 2
        boxes = scene.boxes
    # Кадр для разметки должен содержать ТС, иначе зону не к чему привязывать.
    candidates = [f.frame_idx for f in data.frames if f.frame_idx in boxes]
    if not candidates:
        candidates = [f.frame_idx for f in data.frames]
    frame_idx = min(candidates, key=lambda i: abs(i / data.fps - t))
    box = boxes.get(frame_idx)
    # scene.boxes — тот же самый bbox (окно визита или сырой), который увидит
    # счёт на этом кадре. Вырожденный bbox запрещаем отдавать: on нём
    # toVehicle()/toFrame() во фронтенде делят на нулевую ширину/высоту.
    if box is not None and (box[2] - box[0] < 2 or box[3] - box[1] < 2):
        box = None
    return JSONResponse(
        {
            "stem": stem, "width": data.width, "height": data.height,
            "frame_idx": frame_idx, "ts": round(frame_idx / data.fps, 2),
            "vehicle_box": [float(v) for v in box] if box is not None else None,
            "manual": cfg.manual,
            "doors": [d.model_dump() for d in cfg.doors],
            "visits": [v.model_dump() for v in visits],
        }
    )


@app.get("/api/frame/{stem}")
def api_frame(stem: str, frame_idx: int = 0) -> Response:
    video = _find(stem)
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES, frame_idx)
    ok, frame = cap.read()
    cap.release()
    if not ok:
        raise HTTPException(404, "кадр не читается")
    ok, buf = cv2.imencode(".jpg", frame, [cv2.IMWRITE_JPEG_QUALITY, 85])
    return Response(io.BytesIO(buf.tobytes()).getvalue(), media_type="image/jpeg")


class ZonePayload(BaseModel):
    zones: list[tuple[float, float, float, float]]
    # bbox и номер кадра, которые фронтенд получил из /api/state и относительно
    # которых человек рисовал zones — то же значение, эхом. Нужно, чтобы
    # `paxcount doors remap` знал, к какому bbox привязан пиксельный смысл
    # разметки при следующей смене конвенции координат.
    reference_box: tuple[float, float, float, float] | None = None
    reference_frame: int | None = None


@app.post("/api/zones/{stem}")
def api_zones(stem: str, payload: ZonePayload) -> JSONResponse:
    video = _find(stem)
    cfg = load_config(video)
    cfg.video = video.name
    cfg.manual = True
    cfg.reference_box = payload.reference_box
    cfg.reference_frame = payload.reference_frame
    cfg.doors = [
        DoorSpec(
            door_id=f"door{i + 1}", mode="zone", frame="vehicle",
            line_start=Point(x=z[0], y=z[3]), line_end=Point(x=z[2], y=z[3]),
            zone=(round(z[0], 4), round(z[1], 4), round(z[2], 4), round(z[3], 4)),
        )
        for i, z in enumerate(payload.zones)
    ]
    path = save_config(cfg)
    return JSONResponse({"saved": str(path), "doors": len(cfg.doors)})


@app.get("/api/propose/{stem}")
def api_propose(stem: str, method: str = "pixels") -> JSONResponse:
    """Автопредложение зон дверей — отдаём в редактор, не сохраняя.

    Сохранение идёт только через существующий POST /api/zones, то есть после
    того, как человек посмотрел на предложение и согласился с ним.
    """
    from ..doorprop import propose_doors
    from ..doorprop.frames import pick_proposal_frames
    from ..settings import DOOR_PROPOSAL
    from ..zonecount import BORDER_MARGIN

    if method == "pixels":
        from ..doorprop.pixels import PixelDoorProposer

        proposer = PixelDoorProposer()
    elif method == "openvocab":
        from ..doorprop.openvocab import OpenVocabDoorProposer

        proposer = OpenVocabDoorProposer()
    else:
        raise HTTPException(400, f"неизвестный метод: {method}")

    video = _find(stem)
    data = _tracks(video)
    cfg = load_config(video)
    scene = build_scene(data, cfg)
    if scene.primary is None:
        raise HTTPException(409, "в кадре не найдено ТС — предлагать зоны не от чего")
    if not scene.visits:
        raise HTTPException(409, "не найдено ни одного визита ТС")
    longest = max(scene.visits, key=lambda v: v.departure_ts - v.arrival_ts)
    boxes = scene.boxes_for(longest)
    frames = pick_proposal_frames(data, boxes, longest, k=DOOR_PROPOSAL.reference_frames)
    left = any(
        boxes[i][0] <= BORDER_MARGIN for i in frames if i in boxes
    )
    right = any(
        boxes[i][2] >= data.width - BORDER_MARGIN for i in frames if i in boxes
    )
    try:
        doors = propose_doors(
            proposer, video, data, frames, boxes,
            truncated_left=left, truncated_right=right,
        )
    except FileNotFoundError as exc:  # нет запечённых весов метода A
        raise HTTPException(409, str(exc)) from exc
    return JSONResponse(
        {
            "method": proposer.method_tag,
            "frames": len(frames),
            "zones": [[d.x0, d.y0, d.x1, d.y1] for d in doors],
        }
    )


@app.post("/api/run/{stem}")
def api_run(stem: str) -> JSONResponse:
    video = _find(stem)
    data, _, _ = get_tracks(video)
    cfg = load_config(video)
    result = CustomBackend().run(data, cfg)
    return JSONResponse(
        {
            "boarded": result.boarded, "alighted": result.alighted,
            "visits": [v.model_dump() for v in result.visits],
            "events": [
                {**e.model_dump(), "direction": e.direction.value}
                for e in result.events
            ],
        }
    )
