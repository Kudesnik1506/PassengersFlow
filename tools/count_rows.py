"""Счёт пассажиров моделью по строкам книги — там, где разметки дверей нет.

    uv run python tools/count_rows.py export --reference   # строки со счётом заказчика
    uv run python tools/count_rows.py score                # свести с его счётом

Прежний счёт моделью (`tools/llm_count.py`) резал кроп каждой двери по ручной
разметке. Разметки нет у 290 строк из 299, поэтому здесь пакет — вся сцена
стоянки: кадр целиком без неба, окно вокруг часов камеры из графы P. Какую
машину считать, счётчику говорят маршрутом и примерной секундой: в окне
бывает и соседний автобус.

Способ новый и не замерен, поэтому первый прогон — по строкам, где заказчик
уже вписал «вышло» и «зашло» (`--reference`): их счёт и есть эталон, и ошибка
против него решает, считать ли так остальные.

Счётчик слепой: в пакет не попадает ни счёт заказчика, ни наши прежние числа.
Кадры ложатся только под `out/` и в репозиторий не уходят (запрет 6); наружу —
только числа (решение 018).
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.counting.export import frame_name, refuse_outside  # noqa: E402
from paxcount.counting.parse import AnswerFormatError, consensus, parse_answer  # noqa: E402
from paxcount.counting.prompt import COUNTING_RULES  # noqa: E402
from paxcount.counting.rows import COUNTING, on_camera, on_counting_camera  # noqa: E402
from paxcount.delivery import clocks  # noqa: E402
from paxcount.delivery.timeline import parse_slot  # noqa: E402
from paxcount.delivery.xlsx import BLANK_SHEET, sheet_cells  # noqa: E402
from paxcount.evaluate import count_quality  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR, TARGET_ERROR  # noqa: E402

console = Console(width=170)

ROOT = OUT_DIR / "count-rows"
PACKAGES = ROOT / "пакеты"
ANSWERS = ROOT / "ответы"
BOOK = Path("docs/документы от Заказчика/Tablitsa_dlya_zapolnenia_2026.xlsx")

# Окно вокруг часов строки. Время строки — нажатие оператора, сведённое на
# шкалу камеры; машина подходит за 5-20 с до него и уезжает через 5-15 с
# (просмотр строки 3: подход между −20 и −5, отъезд к +10). Запас — на разброс
# нажатий, 11 с в среднем и до 34 с в крайних случаях.
BEFORE_S = 45.0
AFTER_S = 35.0
# Один кадр в секунду: пакет — вся сцена, а не кроп двери, и человек идёт к
# двери и от неё несколько секунд. Вдвое реже прежнего счёта — ради времени:
# счёт по подписке отклонялся именно по нему (решение 056).
FPS = 1.0
# Небо и крыши счёту не нужны: срезается верх кадра, часы камеры внизу целы.
TOP_CUT = 0.22
WIDTH = 960


def _days_and_offsets(stop: str):
    offsets: dict[str, float] = {}
    days = {}
    for record in clocks.load(DATA_DIR / "clocks" / f"{stop}.csv"):
        offsets.setdefault(record.camera, record.offset_to_reference_s)
        days.setdefault(record.camera, parse_slot(record.file).start.date())
    return offsets, days


def _videos(stop: str, camera: str):
    """Файлы камеры с началом и длиной — чтобы найти, какой покрывает момент."""
    import cv2

    found = []
    for path in sorted((DATA_DIR / "prod_videos").rglob(f"*{stop}_{camera} *.MP4")):
        cap = cv2.VideoCapture(str(path))
        fps = cap.get(cv2.CAP_PROP_FPS) or 30.0
        frames = cap.get(cv2.CAP_PROP_FRAME_COUNT)
        cap.release()
        found.append((path, parse_slot(path.name).start, fps, frames / fps))
    return found


def _covering(videos, moment: datetime):
    for path, start, fps, seconds in videos:
        if start <= moment <= start + timedelta(seconds=seconds):
            return path, start, fps, seconds
    return None


def _prompt(route: str) -> str:
    target = (f"Считать только машину маршрута {route} (номер маршрута виден на "
              "табличке в окне или на табло)" if route and route != "N/A"
              else "Считать только ту машину, что стоит у остановки ближе всего "
                   f"к {BEFORE_S:.0f}-й секунде окна")
    return "\n\n".join([
        "Перед вами кадры с камеры на остановке общественного транспорта. Камера "
        "смотрит вдоль посадочной площадки, машины встают справа от неё.",
        f"Кадры идут подряд, {FPS:g} в секунду; в имени каждого кадра — его секунда "
        "от начала окна, внизу кадра — часы камеры.",
        f"{target}. Она подходит и стоит примерно около {BEFORE_S:.0f}-й секунды "
        "окна (плюс-минус полминуты). Пассажиров других машин, попавших в окно, "
        "не считать.",
        "Задача: сколько человек вошло в эту машину и сколько вышло из неё за "
        "её стоянку.",
        COUNTING_RULES,
        "Не описывать лица, одежду и приметы людей — только числа и время.",
        "Если нужной машины в окне нет, или посадку не видно, так и напишите в "
        '"doubts" и поставьте "found": false. Догадка хуже честного пропуска.',
        "Ответ — только JSON, без пояснений вокруг:\n"
        '{"found": true|false, "boarded": <вошло, целое>, "alighted": <вышло, '
        'целое>, "events": [{"t": <секунда>, "direction": "in"|"out"}], '
        '"doubts": "<что неясно, или пустая строка>"}',
    ])


def _rows(book: Path, reference: bool, only: set[int] | None):
    cells = sheet_cells(book, BLANK_SHEET)[1:]
    for n, cell in enumerate(cells, start=2):
        if only is not None and n not in only:
            continue
        k, l = (cell.get("K") or "").strip(), (cell.get("L") or "").strip()
        if reference and not (k.isdigit() and l.isdigit()):
            continue
        yield n, cell


def _key(cell: dict) -> list[str]:
    return [(cell.get(c) or "").strip() for c in ("C", "D", "G", "P")]


def export(args) -> int:
    import cv2

    refuse_outside(PACKAGES, OUT_DIR)
    offsets, days = _days_and_offsets(args.stop)
    videos = {camera: _videos(args.stop, camera) for camera in COUNTING}
    only = {int(x) for x in args.rows.split(",")} if args.rows else None
    tasks = []
    for n, cell in _rows(args.book, args.reference, only):
        where = on_counting_camera(cell.get("P") or "", offsets, days)
        if where is None:
            console.print(f"[yellow]строка {n}: камеры в графе P нет[/yellow]")
            continue
        camera, moment = where
        hit = _covering(videos[camera], moment)
        if hit is None and camera == "2":
            # К2 в провале — та же стоянка на К3.
            camera, moment = "3", on_camera("2", moment, "3", offsets, days)
            hit = _covering(videos["3"], moment)
        if hit is None:
            console.print(f"[yellow]строка {n}: ни одна счётная камера не писала[/yellow]")
            continue
        path, start, fps, seconds = hit
        package = f"строка-{n:03d}"
        folder = PACKAGES / package
        folder.mkdir(parents=True, exist_ok=True)
        cap = cv2.VideoCapture(str(path))
        first = max((moment - start).total_seconds() - BEFORE_S, 0.0)
        last = min((moment - start).total_seconds() + AFTER_S, seconds)
        cap.set(cv2.CAP_PROP_POS_FRAMES, int(first * fps))
        step = int(round(fps / FPS))
        index, written = 0, 0
        total = int((last - first) * fps)
        while index <= total:
            ok, image = cap.read()
            if not ok:
                break
            if index % step == 0:
                height, width = image.shape[:2]
                crop = image[int(height * TOP_CUT):, :]
                crop = cv2.resize(crop, (WIDTH, int(crop.shape[0] * WIDTH / width)))
                cv2.imwrite(str(folder / frame_name(index / fps)), crop,
                            [cv2.IMWRITE_JPEG_QUALITY, 85])
                written += 1
            index += 1
        cap.release()
        route = (cell.get("H") or "").strip()
        (folder / "задание.md").write_text(_prompt(route), encoding="utf-8")
        tasks.append({"пакет": package, "строка": n, "приметы": _key(cell),
                       "маршрут": route, "камера": camera, "файл": path.stem,
                       "начало_окна": (start + timedelta(seconds=first)).isoformat(),
                       "кадров": written})
        console.print(f"{package}: К{camera} {moment:%H:%M:%S}, маршрут {route or '—'}, "
                       f"кадров {written}")
    (ROOT / "задание.json").write_text(json.dumps(tasks, ensure_ascii=False, indent=2),
                                        encoding="utf-8")
    console.print(f"пакетов {len(tasks)} → {PACKAGES}")
    return 0


def _answer(path: Path):
    body = json.loads(path.read_text(encoding="utf-8"))
    if body.get("found") is False:
        return None
    return parse_answer(json.dumps(body, ensure_ascii=False))


def score(args) -> int:
    tasks = json.loads((ROOT / "задание.json").read_text(encoding="utf-8"))
    cells = sheet_cells(args.book, BLANK_SHEET)[1:]
    theirs = {tuple(_key(c)): c for c in cells}
    table = Table(title="Наш счёт моделью против счёта заказчика")
    for col in ("строка", "время", "маршрут", "вышло: наше/его", "зашло: наше/его",
                 "прогонов", "итог"):
        table.add_column(col, no_wrap=True)
    pairs, disputed, unseen, missing = [], [], [], []
    for task in tasks:
        truth = theirs.get(tuple(task["приметы"]))
        if truth is None:
            missing.append(task["пакет"])
            continue
        folder = ANSWERS / task["пакет"]
        answers, blind = [], 0
        for path in sorted(folder.glob("прогон-*.json")) if folder.exists() else []:
            try:
                got = _answer(path)
            except (AnswerFormatError, json.JSONDecodeError) as why:
                console.print(f"[red]{path}: {why}[/red]")
                continue
            if got is None:
                blind += 1
            else:
                answers.append(got)
        if not answers and not blind:
            continue                    # ещё не считали — не спор и не отказ
        k, l = int(truth["K"]), int(truth["L"])
        agreed = consensus(answers)
        when = f"{truth.get('C')}:{int(truth.get('D') or 0):02d}"
        if agreed.agreed:
            pairs += [(agreed.alighted, k), (agreed.boarded, l)]
            verdict = "сошлось" if (agreed.alighted, agreed.boarded) == (k, l) else "[yellow]расходится[/yellow]"
            ours_out, ours_in = str(agreed.alighted), str(agreed.boarded)
        else:
            if blind and not answers:
                unseen.append(task["пакет"])
                verdict = "[red]машина не найдена[/red]"
            else:
                disputed.append(task["пакет"])
                verdict = "[magenta]прогоны разошлись[/magenta]"
            ours_out = "/".join(str(a.alighted) for a in answers) or "—"
            ours_in = "/".join(str(a.boarded) for a in answers) or "—"
        table.add_row(str(task["строка"]), when, task["маршрут"], f"{ours_out} / {k}",
                       f"{ours_in} / {l}", f"{len(answers) + blind}", verdict)
    console.print(table)
    quality = count_quality(pairs)
    colour = "green" if quality.error <= TARGET_ERROR else "red"
    console.print(f"сошлось прогонов: {len(pairs) // 2} строк; "
                   f"точно {quality.exact:.0%}, ±1 {quality.within_1:.0%}, "
                   f"смещение {quality.bias:+.0%}")
    console.print(f"[{colour}]суммарная погрешность {quality.error:.1%} "
                   f"при планке {TARGET_ERROR:.0%}[/{colour}]")
    if disputed:
        console.print(f"[magenta]нужен третий прогон: {', '.join(disputed)}[/magenta]")
    if unseen:
        console.print(f"[red]машину не нашли: {', '.join(unseen)}[/red]")
    if missing:
        console.print(f"[yellow]строк нет в книге (сменились приметы): {', '.join(missing)}[/yellow]")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="команда", required=True)
    out = sub.add_parser("export", help="нарезать пакеты кадров")
    out.add_argument("--stop", default="22739")
    out.add_argument("--book", type=Path, default=BOOK)
    out.add_argument("--reference", action="store_true",
                      help="только строки, где заказчик вписал «вышло» и «зашло»")
    out.add_argument("--rows", default="", help="номера строк книги через запятую")
    use = sub.add_parser("score", help="свести ответы со счётом заказчика")
    use.add_argument("--book", type=Path, default=BOOK)
    args = parser.parse_args()
    return export(args) if args.команда == "export" else score(args)


if __name__ == "__main__":
    raise SystemExit(main())
