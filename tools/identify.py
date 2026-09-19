"""Опознание машин, которых оператор не записал, по кадру камеры 1.

    uv run python tools/identify.py export --stop 22739
    uv run python tools/identify.py apply  --stop 22739

`export` режет по одному кадру на кандидата — тот, где рамка ТС крупнее всего
и не задевает край (решение 028), — и пишет задание. `apply` сводит ответы
независимых прогонов: поле берётся, только если его назвало строгое
большинство; разошлись — графа остаётся пустой (решения 040, 082).

В кадр уходит КРОП вокруг рамки, а не весь кадр: счётный API ужимает картинку
до полутора тысяч пикселей по длинной стороне, и бортовой номер на кузове
садится под порог читаемости (решение 026).

Размер ТС с камеры 1 не спрашивается вовсе: размер определяется числом дверей,
двери у российских машин справа, а К1 смотрит навстречу и видит морду и левый
борт (решение 030). Ответ о дверях с этой камеры был бы выдумкой.

Кадры ложатся только под `out/` и в репозиторий не уходят (запрет 6).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402

from paxcount.counting.export import refuse_outside  # noqa: E402
from paxcount.delivery.chain import Candidate, Passage, candidates  # noqa: E402
from paxcount.delivery.fill import EXCEL_EPOCH  # noqa: E402
from paxcount.delivery.timeline import parse_slot  # noqa: E402
from paxcount.delivery.visibility import Sighting, sighting_at  # noqa: E402
from paxcount.delivery.xlsx import BLANK_SHEET, sheet_cells  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR  # noqa: E402

console = Console()

IDENTIFY_DIR = OUT_DIR / "identify"
FRAMES_DIR = IDENTIFY_DIR / "кадры"
CHAIN_DIR = OUT_DIR / "chain"

# «Часы К1 минус часы К2»: К1 стоит выше по ходу, машина доезжает до кармана
# почти шесть минут. Подгоняется выравниванием, здесь — затравка.
K1_TO_K2_S = -351.0
# Насколько проезд и стоянка расходятся сверх сдвига. Шире окна — уже соседняя
# машина: на остановке они идут раз в 80–120 с.
WINDOW_S = 60.0
# Запас вокруг рамки в кропе: номер бывает у самого края кузова.
MARGIN_PX = 80


def book_stops(book: Path, camera: str) -> list[datetime]:
    """Моменты камеры из графы P: по ним видно, у какой стоянки есть строка."""
    day = None
    found: list[datetime] = []
    for cell in sheet_cells(book, BLANK_SHEET)[1:]:
        if day is None and (cell.get("B") or "").strip():
            day = _day_of(cell["B"])
        parts = (cell.get("P") or "").split()
        if len(parts) == 2 and parts[0] == f"К{camera}" and day:
            found.append(datetime.combine(day, datetime.strptime(parts[1], "%H:%M:%S").time()))
    return found


def _day_of(value: str):
    """Дата из графы B. Excel держит её числом, а не текстом."""
    text = (value or "").strip()
    for shape in ("%d.%m.%Y", "%Y-%m-%d"):
        try:
            return datetime.strptime(text, shape).date()
        except ValueError:
            continue
    try:
        return EXCEL_EPOCH + timedelta(days=int(float(text)))
    except ValueError:
        return None


def sightings_of(stop: str, camera: str) -> list[Sighting]:
    path = OUT_DIR / "sightings" / f"{stop}-{camera}.csv"
    if not path.exists():
        return []
    with path.open(encoding="utf-8") as handle:
        return sorted(
            (Sighting(start=datetime.fromisoformat(row["start"]),
                       end=datetime.fromisoformat(row["end"]),
                       box=(float(row["x0"]), float(row["y0"]),
                             float(row["x1"]), float(row["y1"])),
                       frame_size=(int(row["width"]), int(row["height"])))
              for row in csv.DictReader(handle)),
            key=lambda s: s.start,
        )


def orphan_passages(stop: str) -> list[Passage]:
    """Проезды из ленты, которым не нашлось нажатия оператора."""
    path = CHAIN_DIR / f"{stop}-лента.csv"
    if not path.exists():
        raise SystemExit(f"нет {path} — сначала tools/chain.py")
    # Рамка лежит в таблице проездов, а не в ленте: лента — про пары, а кроп
    # режется по рамке того кадра, где машина крупнее всего.
    boxes = {}
    seen = CHAIN_DIR / f"{stop}-К1.csv"
    if seen.exists():
        with seen.open(encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                size = tuple(int(n) for n in row["frame_size"].strip("()").split(","))
                boxes[(row["file"], row["track"])] = (
                    json.loads(row["box"]) if row["box"].startswith("[") else None,
                    size,
                )
    found: list[Passage] = []
    with path.open(encoding="utf-8") as handle:
        for row in csv.DictReader(handle):
            if not row["проезд"] or row["оператор"]:
                continue
            peak = datetime.fromisoformat(row["проезд"])
            box, size = boxes.get((row["файл"], str(row["трек"])), (None, (1920, 1080)))
            width = float(row["ширина"])
            found.append(Passage(
                camera="1", file=row["файл"], track=int(row["трек"]),
                start=peak, end=peak + timedelta(seconds=float(row["секунд"])),
                peak=peak,
                box=tuple(box) if box else (0.0, 0.0, width, 0.0),
                frame_size=size,
            ))
    return found


def unclaimed(stop: str, camera: str, book: Path) -> list[Sighting]:
    """Стоянки счётной камеры, у которых нет своей строки в книге."""
    seen = sightings_of(stop, camera)
    taken = {(s.start, s.end) for moment in book_stops(book, camera)
              if (s := sighting_at(moment, seen))}
    return [s for s in seen if (s.start, s.end) not in taken]


def frame_of(candidate: Candidate) -> tuple[bytes, tuple[float, float, float, float]] | None:
    """Кроп вокруг машины из кадра максимальной рамки."""
    import cv2

    videos = {p.stem: p for p in (DATA_DIR / "prod_videos").rglob("*.MP4")}
    video = videos.get(candidate.passage.file)
    if video is None:
        return None
    slot = parse_slot(video.name)
    cap = cv2.VideoCapture(str(video))
    cap.set(cv2.CAP_PROP_POS_FRAMES,
             int((candidate.passage.peak - slot.start).total_seconds() * 30))
    ok, image = cap.read()
    cap.release()
    if not ok:
        return None
    height, width = image.shape[:2]
    x0, y0, x1, y1 = candidate.passage.box
    if x1 <= x0 or y1 <= y0:                 # рамка не сохранилась в ленте
        x0, y0, x1, y1 = 0.0, 0.0, float(width), float(height)
    box = (max(int(x0) - MARGIN_PX, 0), max(int(y0) - MARGIN_PX, 0),
            min(int(x1) + MARGIN_PX, width), min(int(y1) + MARGIN_PX, height))
    crop = image[box[1]:box[3], box[0]:box[2]]
    ok, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, 92])
    return (buffer.tobytes(), (float(box[0]), float(box[1]),
                                float(box[2]), float(box[3]))) if ok else None


def export(args) -> int:
    refuse_outside(FRAMES_DIR, OUT_DIR)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    free = unclaimed(args.stop, args.camera, args.book)
    orphans = orphan_passages(args.stop)
    found = candidates(orphans, free,
                        shift=timedelta(seconds=K1_TO_K2_S),
                        window=timedelta(seconds=WINDOW_S))
    console.print(f"ничьих проездов {len(orphans)}, стоянок без строки {len(free)}, "
                   f"кандидатов {len(found)}")

    tasks = []
    for candidate in found:
        cut = frame_of(candidate)
        if cut is None:
            console.print(f"[yellow]{candidate.passage.peak:%H:%M:%S}: кадр не достался[/yellow]")
            continue
        image, box = cut
        name = f"К1-{candidate.passage.peak:%H-%M-%S}.jpg"
        (FRAMES_DIR / name).write_bytes(image)
        tasks.append({
            "кадр": name,
            "проезд": candidate.passage.peak.isoformat(),
            "файл": candidate.passage.file,
            "трек": candidate.passage.track,
            "стоянка_К2": candidate.sighting.start.isoformat(),
            "кроп": box,
        })
    (IDENTIFY_DIR / "задание.json").write_text(
        json.dumps(tasks, ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"кадров {len(tasks)} → {FRAMES_DIR}")
    console.print(f"задание: {IDENTIFY_DIR / 'задание.json'}")
    return 0


ANSWERS_DIR = IDENTIFY_DIR / "ответы"


def read_answers() -> dict[str, list]:
    """Ответы по кадрам: прогон — это отдельный файл в `ответы/`.

    Так же устроен разбор у кодов таблицы 2: счётчики работают вслепую и
    складывают ответы порознь, а сведение — отдельный шаг.
    """
    from paxcount.counting.identify import parse_identity

    runs: dict[str, list] = {}
    for path in sorted(ANSWERS_DIR.glob("*.json")):
        body = json.loads(path.read_text(encoding="utf-8"))
        for frame, answer in body.items():
            try:
                runs.setdefault(frame, []).append(
                    parse_identity(json.dumps(answer, ensure_ascii=False)))
            except ValueError as why:
                console.print(f"[yellow]{path.name}, {frame}: {why}[/yellow]")
    return runs


def apply(args) -> int:
    from paxcount.counting.identify import agreed_identity
    from paxcount.delivery.model import VehicleKind

    tasks = json.loads((IDENTIFY_DIR / "задание.json").read_text(encoding="utf-8"))
    runs = read_answers()
    if not runs:
        console.print("[red]ответов нет — сначала прогоны по кадрам[/red]")
        return 2

    portal = None
    if args.portal:
        from paxcount.portal import PortalClient, credentials, http_transport
        portal = PortalClient(http_transport(), *credentials())
        cache = OUT_DIR / "portal" / f"{args.stop}.json"
        if cache.exists():
            portal.preload(json.loads(cache.read_text(encoding="utf-8")))

    rows = []
    for task in tasks:
        answers = runs.get(task["кадр"], [])
        agreed, why = agreed_identity(answers)
        row = {
            "кадр": task["кадр"], "проезд": task["проезд"],
            "стоянка_К2": task["стоянка_К2"], "прогонов": len(answers),
            "вид": agreed.kind or "", "борт": agreed.board_number or "",
            "госномер_с_кадра": agreed.state_number or "",
            "маршрут_с_кадра": agreed.route or "",
            "маршрут_портала": "", "госномер_портала": "", "перевозчик": "",
            "причина": "; ".join(f"{k}: {v}" for k, v in sorted(why.items())),
        }
        if portal is not None and agreed.board_number:
            kind = VehicleKind.BUS
            found = portal.lookup(agreed.board_number, args.date, kind,
                                   at=datetime.fromisoformat(task["стоянка_К2"]))
            if getattr(found, "route", None):
                row["маршрут_портала"] = found.route or ""
                row["госномер_портала"] = found.state_number or ""
                row["перевозчик"] = found.carrier or ""
            else:
                row["причина"] = "; ".join(x for x in (row["причина"],
                                                        "портал машину не знает") if x)
        rows.append(row)

    if portal is not None:
        cache = OUT_DIR / "portal" / f"{args.stop}.json"
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(json.dumps(portal.snapshot(), ensure_ascii=False),
                          encoding="utf-8")

    path = IDENTIFY_DIR / "опознание.csv"
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)

    known = sum(1 for r in rows if r["борт"])
    routed = sum(1 for r in rows if r["маршрут_портала"] or r["маршрут_с_кадра"])
    console.print(f"кандидатов {len(rows)}, борт согласован у {known}, "
                   f"маршрут известен у {routed}")
    console.print(f"[dim]{path}[/dim]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="команда", required=True)
    out = sub.add_parser("export", help="нарезать кадры кандидатов")
    out.add_argument("--stop", required=True)
    out.add_argument("--camera", default="2", help="счётная камера, где ищутся стоянки")
    out.add_argument("--book", type=Path,
                      default=OUT_DIR / "2026-09-10-697-22739.xlsx")
    use = sub.add_parser("apply", help="свести ответы прогонов и спросить портал")
    use.add_argument("--stop", required=True)
    use.add_argument("--date", default="2026-09-10", help="дата смены для портала")
    use.add_argument("--portal", action="store_true",
                      help="спросить маршрут и госномер по борту (сеть)")
    args = parser.parse_args()
    return export(args) if args.команда == "export" else apply(args)


if __name__ == "__main__":
    raise SystemExit(main())
