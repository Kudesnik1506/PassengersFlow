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
from collections import Counter
from dataclasses import replace
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
from paxcount.delivery.agreement import Agreement, agree  # noqa: E402
from paxcount.delivery.fill import fill_template  # noqa: E402
from paxcount.delivery.model import Occupancy  # noqa: E402
from paxcount.delivery.operator import (  # noqa: E402
    drop_duplicates, for_stop, read_export, shifts,
)
from paxcount.delivery.sequence import (  # noqa: E402
    Decoded, Entry, clock_shift, merge, shift_around,
)
from paxcount.delivery.timeline import parse_slot  # noqa: E402
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
# Графы строки, которую мы ещё не расшифровали. Время переведено с часов
# оператора, опознание целиком его, счёта нет вовсе — красится всё, что не
# наше: группа, дата и номер ОП (A, B, E) наши в любой строке.
PENDING = {"C", "D", "F", "G", "H", "I", "J", "K", "L"}


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


# Длиннее 31 минуты регистратор не пишет — замер по 125 боевым файлам трёх
# камер (`delivery.timeline`). Момент, отстоящий от начала файла дальше, лежит
# уже не в нём, а в разрыве записи: назвать файл такой строке нечем.
MAX_SLOT_S = 31 * 60


def reference_slots(stop: str, camera: str = "2") -> list:
    """Куски записи опорной камеры, по времени.

    Нужны, чтобы у строки, которую ещё предстоит расшифровать, стояло имя
    файла: расшифровщик должен видеть, где эту машину искать, а правило
    приёмки требует графу N заполненной.
    """
    from paxcount.settings import videos_in

    slots = []
    for path in videos_in(None):
        try:
            slot = parse_slot(path.stem)
        except ValueError:
            continue
        if slot.stop == stop and slot.camera == camera:
            slots.append(slot)
    return sorted(slots)


def video_at(moment, slots) -> str:
    """Файл, внутри которого лежит момент. Пусто — записи на эту минуту нет."""
    covering = [s for s in slots if s.start <= moment]
    if not covering:
        return ""
    slot = covering[-1]
    return slot.name if (moment - slot.start).total_seconds() <= MAX_SLOT_S else ""


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
    parser.add_argument("--only-decoded", action="store_true",
                        help="только расшифрованные машины, без ленты всей смены")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    visits = collect(args.doors, args.stop)
    if not visits:
        console.print("[red]нечего собирать: ни одного визита с разметкой[/red]")
        return 1

    placed = in_route_order(visits, clocks.load(DATA_DIR / "clocks" / f"{args.stop}.csv"))
    rows = book(placed, group=args.group, stop=args.stop, operator=args.operator)

    # Сверка с оператором. Он второе независимое свидетельство, а не истина
    # (решение 025): его данные не подменяют наши, а показывают расхождение.
    # Наполненность — исключение: своей модели для неё нет (решение 032).
    # Дубли снимаются до сверки: повторное нажатие — не второй приезд.
    records = []
    if args.operator_export is not None:
        records = drop_duplicates(
            for_stop(read_export(args.operator_export), args.stop)).kept

    decoded = []
    for item, row in zip(placed, rows):
        deal = agree(row, records) if records else Agreement(None, frozenset())
        if deal.occupancy:
            row = row.model_copy(update={"occupancy": Occupancy(deal.occupancy)})
        decoded.append(Decoded(
            moment=item.reference_ts or item.visit.facts.stop_ts,
            row=row, agreement=deal,
        ))

    # Лента всей смены, а не выборка посчитанных машин (решение 069).
    entries = [Entry(d.moment, d.row, True, d.agreement.record) for d in decoded]
    if records and not args.only_decoded:
        shift = clock_shift(decoded)
        if shift is None:
            console.print("[red]ни одна машина не нашлась у оператора по борту: "
                           "поправку часов измерить нечем, лента не собирается[/red]")
            return 1
        anchor = next(d.agreement.record for d in decoded if d.agreement.record)
        backbone = shift_around(shifts(records), anchor.created)
        # Сверка времени пересчитывается с уже измеренной поправкой: общее для
        # смены расхождение часов книга снимает целиком, и помечать им строку
        # значит утверждать расхождение, которого в ней больше нет.
        decoded = [replace(d, agreement=agree(d.row, records, shift)) for d in decoded]
        entries = merge(decoded, backbone, shift=shift, group=args.group,
                         stop=args.stop, operator=args.operator)
        console.print(f"часы камеры впереди часов оператора на {shift}; "
                       f"смена оператора: {len(backbone)} машин, "
                       f"расшифровано нами {len(decoded)}")

    # Строке, которую ещё предстоит расшифровать, называем файл записи.
    slots = reference_slots(args.stop)
    entries = [
        e if e.decoded else replace(
            e, row=e.row.model_copy(update={"video": video_at(e.moment, slots)}))
        for e in entries
    ]
    rows = [e.row for e in entries]
    deals = {id(d.row): d.agreement for d in decoded}

    highlight: dict[int, set[str]] = {}
    pending: dict[int, set[str]] = {}
    for i, entry in enumerate(entries, start=2):
        if not entry.decoded:
            pending[i] = set(PENDING)
            continue
        deal = deals.get(id(entry.row))
        if deal is None or args.operator_export is None:
            continue
        marks = {FIELD_COLUMN[f] for f in deal.mismatched if f in FIELD_COLUMN}
        if "time" in deal.mismatched:
            marks |= {"C", "D"}
        if deal.missing:
            marks |= UNCONFIRMED
        elif not deal.occupancy:
            marks.add("I")
        if marks:
            highlight[i] = marks

    table = Table(title="Лента смены: строка на каждое ТС, время на шкале камеры")
    for column in ("строка", "время", "маршрут", "борт", "номер", "размер",
                    "запол", "вышло", "зашло", "код", "источник", "сверка"):
        table.add_column(column, no_wrap=True)
    for i, entry in enumerate(entries, start=2):
        row, deal = entry.row, deals.get(id(entry.row))
        if not entry.decoded:
            verdict, source = "", "[dim]оператор[/dim]"
        else:
            source = "наш счёт"
            if deal is None or deal.record is None:
                verdict = "[yellow]записи нет[/yellow]"
            elif deal.mismatched:
                verdict = "[yellow]" + ", ".join(sorted(deal.mismatched)) + "[/yellow]"
            else:
                verdict = "сошлось"
        table.add_row(
            str(i), entry.moment.strftime("%H:%M:%S"), row.route or "",
            row.board_number or "", row.number or "",
            row.size.value if row.size else "",
            row.occupancy.value if row.occupancy else "",
            "N/A" if row.alighted is None else str(row.alighted),
            "N/A" if row.boarded is None else str(row.boarded),
            str(row.comment or ""), source, verdict,
        )
    console.print(table)

    # Замечания приёмки по нерасшифрованным строкам не перечисляются поимённо:
    # их сотня, и они все об одном — работа не сделана. Поимённо только те,
    # где мы уже что-то утверждаем.
    not_ours = {i for i, e in enumerate(entries, start=2) if not e.decoded}
    problems = validate(rows)
    bulk: Counter = Counter()
    for problem in problems:
        if problem.row in not_ours:
            bulk[problem.field] += 1
            continue
        where = "вся книга" if problem.row == 0 else f"строка {problem.row}"
        console.print(f"[yellow]{where}, {problem.field}:[/yellow] {problem.message}")
    for field, count in sorted(bulk.items()):
        console.print(f"[dim]нерасшифрованных строк без «{field}»: {count}[/dim]")

    args.out.mkdir(parents=True, exist_ok=True)
    name = book_filename(rows, group=args.group, stop=args.stop)
    if args.template is not None:
        written = fill_template(args.template, rows, args.out / name,
                                 highlight=highlight, pending=pending)
    else:
        written = write_rows(args.out / name, rows)
    console.print(f"книга: {written}")
    if problems:
        console.print(f"[yellow]правил нарушено: {len(problems)}, из них по "
                       f"нерасшифрованным строкам {sum(bulk.values())}[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
