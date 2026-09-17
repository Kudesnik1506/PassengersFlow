"""Итоговая книга заказчика: лента машин с нашим счётом.

    uv run python tools/deliver.py --group 1317 --operator "Иванов Иван"

Соединяет то, что до сих пор жило порознь: счёт по дверям (`bench.counting`) и
форму поставки (`paxcount.delivery`). Считает те визиты, на которые есть ручная
разметка дверей, ставит их в одну ленту по общей шкале времени и пишет книгу в
формате заказчика.

Порядок строк — не сортировка по «своему» времени: часы камер идут врозь, и
лента собирается только на общей шкале (`delivery.assemble`). Визит, который на
неё не встал, из книги не выбрасывается — уходит в конец с примечанием.

Наружу уходят числа и графы бланка (решение 018): кадры не сохраняются.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import case_from_layout  # noqa: E402
from paxcount.bench.counting import (  # noqa: E402
    DETECT_WINDOW_S, count_doors, stop_window,
)
from paxcount.bench.summary import match_row  # noqa: E402
from paxcount.bench.tracks import tracks_in_windows  # noqa: E402
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.core.video import probe  # noqa: E402
from paxcount.delivery import clocks  # noqa: E402
from paxcount.delivery.assemble import (  # noqa: E402
    Visit, book, book_filename, in_route_order,
)
from paxcount.delivery.model import VehicleKind  # noqa: E402
from paxcount.delivery.reconcile import VisitFacts  # noqa: E402
from paxcount.delivery.agreement import agree  # noqa: E402
from paxcount.delivery.fill import fill_template  # noqa: E402
from paxcount.delivery.model import Occupancy  # noqa: E402
from paxcount.delivery.operator import for_stop, read_export  # noqa: E402
from paxcount.delivery.validate import validate  # noqa: E402
from paxcount.delivery.xlsx import write_rows  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR  # noqa: E402
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount import truth_rows  # noqa: E402

console = Console(width=170)

# Поле сверки → графа бланка. Время занимает две графы, поэтому его здесь нет:
# оно разворачивается в C и D там, где строится пометка.
FIELD_COLUMN = {"kind": "F", "route": "H", "size": "J", "board": "G"}
# Графы, которые подтверждал бы оператор, будь у него запись. Когда он молчит,
# подтверждения нет ни у одной из них — и это не то же самое, что совпадение.
UNCONFIRMED = {"F", "G", "H", "I", "J"}


def _kind(text: str | None) -> VehicleKind | None:
    try:
        return VehicleKind(text) if text else None
    except ValueError:
        return None


def collect(doors: Path, stop: str) -> list[Visit]:
    """Визиты с ручной разметкой дверей — посчитанные нашим счётчиком."""
    rows = truth_rows.load(DATA_DIR / "truth" / "rows.csv")
    from paxcount.settings import videos_in

    visits: list[Visit] = []
    for path in sorted(doors.rglob("*.json")):
        layout = load_door_layout(path)
        if layout.camera not in COUNTING_CAMERAS:
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            console.print(f"[yellow]пропущено:[/yellow] {problem}")
            continue
        video = next((p for p in videos_in(None) if p.stem == case.video), None)
        if video is None:
            console.print(f"[yellow]нет файла {case.video}[/yellow]")
            continue
        half = int(round(DETECT_WINDOW_S * (probe(video).fps or 30.0)))
        key = f"v{case.visit_key}"
        window = (max(0, case.frame_idx - half), case.frame_idx + half)
        data = tracks_in_windows(video, {key: window})[key]
        stop_win = stop_window(data, case.body_px)
        # Окно стоянки не нашлось — машину мы видели, а счесть не смогли.
        # Строка остаётся, графы счёта пустуют (правило владельца).
        boarded = alighted = None
        if stop_win is not None:
            per_door = count_doors(data, layout, stop_win)
            boarded = sum(v[0] for v in per_door.values())
            alighted = sum(v[1] for v in per_door.values())
        moment = visit_moment(layout.visit_key)
        row = match_row(layout.camera, moment, rows)
        visits.append(Visit(
            facts=VisitFacts(
                visit_key=layout.visit_key, camera=layout.camera, stop_ts=moment,
                layout=layout, boarded=boarded, alighted=alighted,
                route=row.route if row else None,
                board_number=row.board_number if row else None,
                state_number=row.state_number if row else None,
                kind=_kind(row.vehicle_kind) if row else None,
                size=row.size if row else None,
            ),
            file=case.video,
        ))
    return visits


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doors", type=Path, default=DATA_DIR / "truth" / "doors")
    parser.add_argument("--stop", default="22739", help="номер ОП — графа E бланка")
    parser.add_argument("--group", default="", help="группа ОП — графа A бланка")
    parser.add_argument("--operator", default="", help="фамилия расшифровщика")
    parser.add_argument("--operator-export", type=Path, default=None,
                        help="выгрузка оператора: сверка и наполненность")
    parser.add_argument("--template", type=Path, default=None,
                        help="шаблон заказчика: книга пишется по нему, со списками и стилями")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    visits = collect(args.doors, args.stop)
    if not visits:
        console.print("[red]нечего собирать: ни одного визита с разметкой[/red]")
        return 1

    records = clocks.load(DATA_DIR / "clocks" / f"{args.stop}.csv")
    placed = in_route_order(visits, records)
    rows = book(placed, group=args.group, stop=args.stop, operator=args.operator)

    # Сверка с оператором. Он второе независимое свидетельство, а не истина
    # (решение 025): его данные не подменяют наши, а показывают расхождение.
    # Наполненность — исключение: своей модели для неё нет (решение 032).
    highlight: dict[int, set[str]] = {}
    agreements = []
    if args.operator_export is not None:
        records = for_stop(read_export(args.operator_export), args.stop)
        for i, row in enumerate(rows, start=2):
            result = agree(row, records)
            agreements.append(result)
            if result.occupancy:
                rows[i - 2] = row.model_copy(
                    update={"occupancy": Occupancy(result.occupancy)})
            marks = {FIELD_COLUMN[f] for f in result.mismatched if f in FIELD_COLUMN}
            if "time" in result.mismatched:
                marks |= {"C", "D"}
            if result.missing:
                marks |= UNCONFIRMED
            elif not result.occupancy:
                marks.add("I")
            if marks:
                highlight[i] = marks
    else:
        agreements = [None] * len(rows)

    table = Table(title="Лента визитов в порядке общей шкалы")
    for column in ("№", "камера", "своё время", "общая шкала", "маршрут", "номер",
                    "размер", "запол", "вышло", "зашло", "код", "оператор"):
        table.add_column(column, no_wrap=True)
    for i, (item, row, deal) in enumerate(zip(placed, rows, agreements), start=1):
        if deal is None:
            verdict = "—"
        elif deal.missing:
            verdict = "[yellow]записи нет[/yellow]"
        elif deal.mismatched:
            verdict = "[yellow]" + ", ".join(sorted(deal.mismatched)) + "[/yellow]"
        else:
            verdict = "сошлось"
        table.add_row(
            str(i), item.visit.facts.camera,
            item.visit.facts.stop_ts.strftime("%H:%M:%S"),
            item.reference_ts.strftime("%H:%M:%S") if item.reference_ts else "—",
            row.route or "", row.number or "", row.size.value if row.size else "",
            row.occupancy.value if row.occupancy else "",
            "N/A" if row.alighted is None else str(row.alighted),
            "N/A" if row.boarded is None else str(row.boarded),
            str(row.comment or ""), verdict,
        )
    console.print(table)

    problems = validate(rows)
    for problem in problems:
        where = "вся книга" if problem.row == 0 else f"строка {problem.row}"
        console.print(f"[yellow]{where}, {problem.field}:[/yellow] {problem.message}")

    args.out.mkdir(parents=True, exist_ok=True)
    name = book_filename(rows, group=args.group, stop=args.stop)
    if args.template is not None:
        written = fill_template(args.template, rows, args.out / name,
                                 highlight=highlight)
    else:
        written = write_rows(args.out / name, rows)
    console.print(f"книга: {written}")
    if problems:
        console.print(f"[yellow]правил нарушено: {len(problems)} — "
                       "книга записана, но заказчик её вернёт[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
