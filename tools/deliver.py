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
import json
import sys
from collections import Counter
from dataclasses import replace
from datetime import datetime, timedelta
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
from paxcount.delivery.fill import (  # noqa: E402
    carried_over, fill_template, orphaned,
)
from paxcount.delivery.marking import (  # noqa: E402
    marks_for, marks_for_duplicate, marks_for_our_measurement, marks_for_repair,
)
from paxcount.delivery.model import Occupancy  # noqa: E402
from paxcount.delivery.plates import with_plate  # noqa: E402
from paxcount.delivery.operator import (  # noqa: E402
    drop_duplicates, for_stop, read_export,
)
from paxcount.delivery.sequence import (  # noqa: E402
    Decoded, Entry, backbone, clock_shift, merge,
)
from paxcount.delivery.coverage import gaps_of_cameras, with_footage  # noqa: E402
from paxcount.delivery.timeline import CameraTrack, parse_slot  # noqa: E402
from paxcount.delivery.validate import validate  # noqa: E402
from paxcount.delivery.xlsx import BLANK_SHEET, sheet_cells, write_rows  # noqa: E402
from paxcount import portal  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR  # noqa: E402
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount import truth_rows  # noqa: E402

console = Console(width=170)



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


def reference_slots(stop: str, camera: str = "2") -> list:
    """Куски записи камеры с ИЗМЕРЕННОЙ длительностью, по времени.

    Нужны, чтобы у строки, которую ещё предстоит расшифровать, стояло имя
    файла: расшифровщик должен видеть, где машину искать. Длительность
    измеряется, а не берётся по расстоянию до соседа, — между файлами боевой
    К2 есть дыры, и момент в дыре не снят вовсе (`timeline.file_at`).
    """
    from paxcount.settings import videos_in

    found = []
    for path in videos_in(None):
        try:
            slot = parse_slot(path.stem)
        except ValueError:
            continue
        if slot.stop == stop and slot.camera == camera:
            found.append((slot, path))
    found.sort(key=lambda pair: pair[0].start)

    slots = []
    for n, (slot, path) in enumerate(found):
        meta = probe(path)
        seconds = meta.frame_count / (meta.fps or 30.0)
        to_next = (found[n + 1][0].start - slot.start).total_seconds() \
            if n + 1 < len(found) else seconds
        slots.append(replace(slot, duration_s=to_next, real_duration_s=seconds))
    return slots


# Порядок предпочтения камер. Сначала считающие (решение 034: К2 и К3 видят
# двери), К1 последней — она в счёте не участвует и нужна только как свидетель
# там, где остальные молчали.
CAMERA_ORDER = ("2", "3", "1")


def camera_tracks(stop: str) -> list[CameraTrack]:
    """Ленты всех трёх камер с поправкой к общей шкале.

    Поправка берётся на КАМЕРУ, хотя `clocks` хранит её на файл. Допущение
    названо в `timeline.CameraTrack` и держится на решении 027: смена — одна
    сплошная запись, нарезанная одними часами. Камера без замера поправки в
    список не входит вовсе: назвать её файл, не умея перевести время, значит
    отправить проверяющего не туда.
    """
    records = clocks.load(DATA_DIR / "clocks" / f"{stop}.csv")
    offsets = {r.camera: r.offset_to_k2_s for r in records}
    out = []
    for camera in CAMERA_ORDER:
        if camera not in offsets:
            console.print(f"[yellow]камера {camera}: поправки часов нет, "
                           "в книгу её файлы не попадут[/yellow]")
            continue
        out.append(CameraTrack(camera=camera, offset_to_k2_s=offsets[camera],
                                slots=reference_slots(stop, camera)))
    return out


def ask_the_portal(entries: list, stop: str) -> list:
    """Меняет бортовые номера на государственные — дорогой решения 026.

    Ответы кладутся в `out/` и поднимаются оттуда на следующем прогоне: портал
    государственный, доступ общий на нескольких расшифровщиков, а книга
    пересобирается по многу раз. В репозиторий кэш не уходит — там настоящие
    госномера (решение 018), а `out/` закрыт `.gitignore`.
    """
    cache_path = OUT_DIR / "portal" / f"{stop}.json"
    login, password = portal.credentials()
    client = portal.PortalClient(portal.http_transport(), login, password)
    if cache_path.exists():
        client.preload(json.loads(cache_path.read_text(encoding="utf-8")))
        console.print(f"кэш портала: {len(client.snapshot())} машин из {cache_path}")

    asked = {e.row.board_number for e in entries
              if e.row.board_number and not e.row.state_number}
    console.print(f"портал: спрашиваем {len(asked)} бортовых номеров")
    out = []
    for n, entry in enumerate(entries, start=1):
        try:
            row, changed = with_plate(entry.row, entry.moment, client.lookup)
        except Exception as exc:               # сеть, учётка, форма ответа
            console.print(f"[red]портал замолчал на {entry.row.board_number}: {exc}[/red]")
            out.extend(entries[n - 1:])
            break
        # Подстановка госномера — наша правка чужой графы, и метится она так же,
        # как починка перепутанных полей (решения 073 и 076).
        out.append(replace(entry, row=row, repaired=entry.repaired | changed))
        if n % 25 == 0:
            console.print(f"  строка {n} из {len(entries)}")
    cache_path.parent.mkdir(parents=True, exist_ok=True)
    cache_path.write_text(json.dumps(client.snapshot(), ensure_ascii=False),
                           encoding="utf-8")
    found = sum(1 for e in out if e.row.state_number)
    console.print(f"госномеров в книге: {found} из {len(out)} строк")
    return out


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
    parser.add_argument("--portal", action="store_true",
                        help="спросить госномера у портала по бортовым (сеть, учётка из .env)")
    parser.add_argument("--only-decoded", action="store_true",
                        help="только расшифрованные машины, без ленты всей смены")
    parser.add_argument("--book", type=Path, default=None,
                         help="книга заказчика: читается ради ручного ввода и "
                               "перезаписывается ею же — копировать руками нельзя")
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
    # Повторные нажатия из книги НЕ выбрасываются (решение 075): они остаются
    # строками и красятся розовым. Сверка при этом идёт по первым нажатиям —
    # иначе наша строка могла бы слиться с повтором, а первое нажатие осталось
    # бы отдельной машиной.
    records: list = []
    canonical: list = []
    duplicate_ids: set[int] = set()
    if args.operator_export is not None:
        records = for_stop(read_export(args.operator_export), args.stop)
        dedup = drop_duplicates(records)
        canonical = dedup.kept
        duplicate_ids = {id(r) for r in dedup.dropped}

    decoded = []
    for item, row in zip(placed, rows):
        deal = agree(row, canonical) if canonical else Agreement(None, frozenset())
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
        # Костяк — весь день, а не окно съёмки: файл заказчика принимается на
        # дату, и в принятом лежат все три окна разом (решение 072).
        day = decoded[0].moment.date()
        spine = backbone(records, day)
        # Сверка времени пересчитывается с уже измеренной поправкой: общее для
        # смены расхождение часов книга снимает целиком, и помечать им строку
        # значит утверждать расхождение, которого в ней больше нет.
        decoded = [replace(d, agreement=agree(d.row, canonical, shift)) for d in decoded]
        entries = merge(decoded, spine, shift=shift, group=args.group,
                         stop=args.stop, operator=args.operator)
        console.print(f"часы камеры впереди часов оператора на {shift}; "
                       f"за {day:%d.%m.%Y} у оператора {len(spine)} записей, "
                       f"из них повторных нажатий {len(duplicate_ids)}, "
                       f"расшифровано нами {len(decoded)}")

    # Строке, которую ещё предстоит расшифровать, называем файл записи и часы
    # той камеры, что его сняла. Камер три: К2 теряет 1074 с в главный провал,
    # а К1 в это время писала без единого разрыва, и молчать об этом значило бы
    # выдать «не снято» там, где снято другой камерой.
    tracks = camera_tracks(args.stop)
    gaps = gaps_of_cameras(tracks)
    entries = [e if e.decoded else replace(e, row=with_footage(e.row, e.moment,
                                                                tracks, gaps))
                for e in entries]
    if args.portal:
        entries = ask_the_portal(entries, args.stop)

    rows = [e.row for e in entries]

    # Строка оператора, которую мы не расшифровывали, не красится вовсе: она
    # целиком его, спорить в ней не с чем. Красятся только наши (решение 070).
    highlight: dict[int, set[str]] = {}
    duplicates: dict[int, set[str]] = {}
    for i, entry in enumerate(entries, start=2):
        if entry.record is not None and id(entry.record) in duplicate_ids:
            duplicates[i] = marks_for_duplicate()
        # Графы, которые мы изменили за оператором, и графы, которых у него
        # нет вовсе (файл, камера, комментарий), — в ЛЮБОЙ строке: принцип 9
        # мерит происхождение, а не спор.
        marks = marks_for_repair(entry.repaired) | marks_for_our_measurement(entry.row)
        if entry.decoded and args.operator_export is not None:
            marks |= marks_for(entry.agreement)
        if marks:
            highlight[i] = marks

    table = Table(title="Лента смены: строка на каждое ТС, время на шкале камеры")
    for column in ("строка", "время", "маршрут", "борт", "номер", "размер",
                    "запол", "вышло", "зашло", "код", "источник", "сверка"):
        table.add_column(column, no_wrap=True)
    for i, entry in enumerate(entries, start=2):
        row, deal = entry.row, entry.agreement
        if not entry.decoded:
            verdict = ""
            source = ("[magenta]повтор[/magenta]"
                       if entry.record is not None and id(entry.record) in duplicate_ids
                       else "[dim]оператор[/dim]")
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
    expected = 0
    for problem in problems:
        if problem.row in not_ours:
            bulk[problem.field] += 1
            continue
        # «ТС встречается дважды» — это и есть помеченные розовым повторы.
        # Перечислять их поимённо незачем: они в книге намеренно (решение 075).
        if problem.row == 0 and problem.field == "number" and duplicates:
            expected += 1
            continue
        where = "вся книга" if problem.row == 0 else f"строка {problem.row}"
        console.print(f"[yellow]{where}, {problem.field}:[/yellow] {problem.message}")
    for field, count in sorted(bulk.items()):
        console.print(f"[dim]нерасшифрованных строк без «{field}»: {count}[/dim]")
    if expected:
        console.print(f"[dim]замечаний «ТС дважды в одну минуту»: {expected} — "
                       "это повторные нажатия оператора, помеченные розовым[/dim]")

    args.out.mkdir(parents=True, exist_ok=True)
    name = book_filename(rows, group=args.group, stop=args.stop)
    # Клетки, которые заказчик заполнял руками. Наша сборка их не пишет —
    # значит и стереть не вправе: наполненность по прибытию мы не считаем
    # вовсе, а в книге она стоит.
    keep: dict = {}
    lost: list = []
    previous = args.book if args.book is not None else args.out / name
    if previous.exists():
        was = sheet_cells(previous, BLANK_SHEET)[1:]
        keep = carried_over(was, rows)
        lost = orphaned(was, rows)
        cells = sum(len(v) for v in keep.values())
        console.print(f"перенесено из {previous.name}: {cells} клеток ручного "
                       f"ввода в {len(keep)} строках")
    for cell in lost:
        console.print("[red]строка прошлой книги не нашла места: "
                       + ", ".join(f"{k}={v}" for k, v in sorted(cell.items())) + "[/red]")

    targets = [args.out / name]
    if args.book is not None:
        # Книга заказчика перезаписывается ТОЛЬКО когда переносить нечего сверх
        # найденного: строка, потерявшая приметы, унесёт с собой его ручной ввод,
        # а восстановить его будет неоткуда (решение 079).
        if lost:
            console.print(f"[red]книга заказчика не тронута: {len(lost)} строк "
                           "прошлой книги не сопоставлены, ручной ввод в них "
                           "пропал бы[/red]")
        else:
            targets.append(args.book)
    for target in targets:
        if args.template is not None:
            written = fill_template(args.template, rows, target,
                                     highlight=highlight, duplicates=duplicates,
                                     keep=keep)
        else:
            written = write_rows(target, rows)
        console.print(f"книга: {written}")
    if problems:
        console.print(f"[yellow]правил нарушено: {len(problems)}, из них по "
                       f"нерасшифрованным строкам {sum(bulk.values())}[/yellow]")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
