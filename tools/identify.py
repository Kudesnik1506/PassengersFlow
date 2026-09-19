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
from dataclasses import replace
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

# Насколько проезд и стоянка расходятся на ОБЩЕЙ шкале. Часы камер сведены
# замеренными поправками (`data/clocks`), поэтому сдвига между ними больше нет,
# остаётся ход машины от К1 до кармана и разброс. Шире окна — уже соседняя
# машина: на остановке они идут раз в 80–120 с.
WINDOW_S = 90.0
# Пустота в стоянках, после которой считается, что счётные камеры ослепли.
# Машины идут через 80–120 с, поэтому три минуты тишины — не затишье.
BLIND_SPAN_S = 180.0
# Счётные камеры: обе смотрят карман, и подтвердить стоянку может любая.
COUNTING = ("2", "3")
# Запас вокруг рамки в кропе: номер бывает у самого края кузова.
MARGIN_PX = 80
# Насколько близко должна стоять строка книги с тем же номером, чтобы считать
# заезд уже записанным. Машины идут раз в 80-120 с, а тот же борт возвращается
# на круг через часы — трёх минут хватает, чтобы различить одно от другого.
DUPLICATE_WINDOW_S = 180.0


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


def camera_scale() -> dict[str, tuple[float, object]]:
    """Поправка часов и настоящая дата — на камеру, а не на файл.

    Поправка на файл есть у одной записи из двадцати двух (`data/clocks`), а
    смена снята сплошняком (решение 027), поэтому для сведения шкал берётся
    поправка камеры. У К3 в именах файлов стоит август вместо сентября — дата
    чинится тем же `date_override`.
    """
    from paxcount.delivery import clocks

    scale: dict[str, tuple[float, object]] = {}
    for record in clocks.load(DATA_DIR / "clocks" / "22739.csv"):
        scale.setdefault(record.camera,
                          (record.offset_to_reference_s, record.date_override))
    return scale


def to_scale(moment: datetime, camera: str,
              scale: dict[str, tuple[float, object]]) -> datetime:
    """Час камеры — на общую шкалу смены."""
    offset, real_day = scale.get(camera, (0.0, None))
    if real_day:                       # у К3 в именах файлов август вместо сентября
        day = (real_day if hasattr(real_day, "year")
                else datetime.strptime(str(real_day), "%Y-%m-%d").date())
        moment = datetime.combine(day, moment.time())
    return moment - timedelta(seconds=offset)


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


def on_scale(sightings: list[Sighting], camera: str, scale) -> list[Sighting]:
    """Те же стоянки, но на общей шкале смены."""
    return [Sighting(start=to_scale(s.start, camera, scale),
                     end=to_scale(s.end, camera, scale),
                     box=s.box, frame_size=s.frame_size)
             for s in sightings]


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
    from paxcount.delivery.chain import blind_windows

    refuse_outside(FRAMES_DIR, OUT_DIR)
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    scale = camera_scale()

    # Стоянку подтверждает любая счётная камера: таблица К2 неполна (42 файла
    # из 48), и требовать подтверждения именно от неё значит терять машины там,
    # где она ослепла.
    free: list[Sighting] = []
    all_stops: list[datetime] = []
    for camera in COUNTING:
        seen = sightings_of(args.stop, camera)
        if not seen:
            console.print(f"[yellow]стоянок камеры {camera} нет — "
                           "подтверждать нечем[/yellow]")
            continue
        all_stops += [to_scale(s.start, camera, scale) for s in seen]
        free += on_scale(unclaimed(args.stop, camera, args.book), camera, scale)

    blind = blind_windows(all_stops, span=timedelta(seconds=BLIND_SPAN_S))
    # Сопоставление идёт на общей шкале, а кадр режется по часам своей камеры:
    # перемотка записи ведётся по ним, и подменять их шкалой нельзя.
    свои = orphan_passages(args.stop)
    сырое = {(p.file, p.track): p for p in свои}
    orphans = [replace(p,
                        start=to_scale(p.start, "1", scale),
                        end=to_scale(p.end, "1", scale),
                        peak=to_scale(p.peak, "1", scale))
                for p in свои]
    found = candidates(orphans, free, shift=timedelta(0),
                        window=timedelta(seconds=WINDOW_S), blind=blind)
    console.print(f"ничьих проездов {len(orphans)}, стоянок без строки {len(free)}, "
                   f"слепых окон {len(blind)}, кандидатов {len(found)}")

    tasks = []
    for candidate in found:
        candidate = replace(candidate,
                             passage=сырое[(candidate.passage.file,
                                             candidate.passage.track)])
        cut = frame_of(candidate)
        if cut is None:
            console.print(f"[yellow]{candidate.passage.peak:%H:%M:%S}: кадр не достался[/yellow]")
            continue
        image, box = cut
        # Трек в имени обязателен: два соседних проезда дают один и тот же
        # момент пика, и без него второй кадр затирает первый.
        name = f"К1-{candidate.passage.peak:%H-%M-%S}-т{candidate.passage.track}.jpg"
        (FRAMES_DIR / name).write_bytes(image)
        tasks.append({
            "кадр": name,
            "проезд": candidate.passage.peak.isoformat(),
            "файл": candidate.passage.file,
            "трек": candidate.passage.track,
            "стоянка": (candidate.sighting.start.isoformat()
                         if candidate.sighting else ""),
            "на_шкале": to_scale(candidate.passage.peak, "1", scale).isoformat(),
            "оговорка": candidate.note,
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


def book_rows(book: Path, camera: str) -> list[tuple[datetime, str]]:
    """Строки книги как пары «час камеры, номер ТС» — для проверки на дубль."""
    day = None
    found: list[tuple[datetime, str]] = []
    for cell in sheet_cells(book, BLANK_SHEET)[1:]:
        if day is None and (cell.get("B") or "").strip():
            day = _day_of(cell["B"])
        parts = (cell.get("P") or "").split()
        number = (cell.get("G") or "").strip()
        if len(parts) == 2 and parts[0] == f"К{camera}" and day and number:
            found.append((datetime.combine(
                day, datetime.strptime(parts[1], "%H:%M:%S").time()), number))
    return found


def apply(args) -> int:
    from paxcount.counting.identify import (
        agreed_identity, kind_by_portal, public_kind,
    )
    from paxcount.delivery.chain import already_known
    from paxcount.delivery.model import VehicleKind

    tasks = json.loads((IDENTIFY_DIR / "задание.json").read_text(encoding="utf-8"))
    # Строки книги приводятся к общей шкале: сверка идёт на ней, а графа P
    # хранит часы своей камеры.
    scale = camera_scale()
    written = [(to_scale(moment, camera, scale), number)
                for camera in COUNTING
                for moment, number in book_rows(args.book, camera)]
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
            "стоянка": task.get("стоянка", ""), "на_шкале": task["на_шкале"],
            "оговорка": task.get("оговорка", ""), "прогонов": len(answers),
            "вид": agreed.kind or "", "борт": agreed.board_number or "",
            "госномер_с_кадра": agreed.state_number or "",
            "маршрут_с_кадра": agreed.route or "",
            "маршрут_портала": "", "госномер_портала": "", "перевозчик": "",
            "уже_в_книге": "",
            "причина": "; ".join(f"{k}: {v}" for k, v in sorted(why.items())),
        }
        if portal is not None and agreed.board_number:
            kind = VehicleKind.BUS
            found = portal.lookup(agreed.board_number, args.date, kind,
                                   at=datetime.fromisoformat(task["на_шкале"]))
            if getattr(found, "route", None):
                row["маршрут_портала"] = found.route or ""
                row["госномер_портала"] = found.state_number or ""
                row["перевозчик"] = found.carrier or ""
            else:
                row["причина"] = "; ".join(x for x in (row["причина"],
                                                        "портал машину не знает") if x)
        # Последняя проверка: не записан ли этот заезд оператором. Выравнивание
        # ошибается, и четыре кандидата из двенадцати на боевом утре оказались
        # уже стоящими в книге — вторая строка на тот же заезд бракует файл.
        # Вид: слово модели в словарь заказчика, а спор о нём разрешает портал.
        вид = public_kind(agreed.kind)
        по_порталу = kind_by_portal(board=row["борт"] or None,
                                     state=row["госномер_портала"] or None)
        if по_порталу is not None and по_порталу is not вид:
            row["причина"] = "; ".join(x for x in (
                row["причина"],
                f"вид с кадра «{agreed.kind or 'не назван'}», "
                f"портал дал госномер — {по_порталу.value}") if x)
            вид = по_порталу
        row["вид"] = вид.value if вид else ""
        # Грузовой фургон в книгу не идёт: она про общественный транспорт.
        if agreed.kind and вид is None:
            row["уже_в_книге"] = "не ОТ"
        номер = row["госномер_портала"] or row["борт"]
        if номер and already_known(datetime.fromisoformat(task["на_шкале"]),
                                    номер, written,
                                    window=timedelta(seconds=DUPLICATE_WINDOW_S)):
            row["уже_в_книге"] = "да"
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
    dupes = sum(1 for r in rows if r["уже_в_книге"] == "да")
    alien = sum(1 for r in rows if r["уже_в_книге"] == "не ОТ")
    console.print(f"кандидатов {len(rows)}, борт согласован у {known}, "
                   f"маршрут известен у {routed}")
    console.print(f"[yellow]уже стоят в книге: {dupes} — оператор их записал, "
                   "выравнивание не спарило[/yellow]")
    console.print(f"не общественный транспорт: {alien}")
    console.print(f"новых строк выйдет: {len(rows) - dupes - alien}")
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
    use.add_argument("--camera", default="2", help="счётная камера кандидатов")
    use.add_argument("--book", type=Path,
                      default=OUT_DIR / "2026-09-10-697-22739.xlsx")
    args = parser.parse_args()
    return export(args) if args.команда == "export" else apply(args)


if __name__ == "__main__":
    raise SystemExit(main())
