"""Выгрузка оператора: приём, очистка, разбиение на смены.

Оператор стоит на остановке и жмёт кнопку на каждую подъехавшую машину. Его
таблица приходит к нам вместе с видео и долгое время считалась готовым списком
(решение 025). Боевая выгрузка это опровергла: заказчик прямо сказал, что
оператор мог машину и пропустить, а замер по остановке 22739 показал, что из
292 записей 50 сделаны дважды и 5 испорчены при вводе.

Отсюда устройство модуля: выгрузка принимается как **черновик**. Ничего не
отбрасывается молча — снятый дубль и починенная строка остаются в ответе
отдельными списками, потому что каждую из них потом придётся объяснить, если
заказчик оспорит строку.

Почему перестановка полей чинится без портала. Формы взаимно однозначны: борт —
четыре-пять цифр, маршрут — одна-три с возможной буквой. Строка «борт 225 /
маршрут 38201» второго прочтения не имеет, и поход в государственный портал
ничего к ней не добавит. А вот «382252» — шесть цифр, и кандидата два (38225 и
38252); такую чинить молча нельзя, она уходит в ручной разбор видимой.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from datetime import date as Date
from datetime import datetime
from pathlib import Path

from .xlsx import sheet_cells

# Формы полей, замеренные по боевой выгрузке: 288 бортов из 292 — четыре или
# пять цифр, маршрут — одна-три цифры с возможной буквой («2В»).
BOARD_RE = re.compile(r"\d{4,5}")
ROUTE_RE = re.compile(r"\d{1,3}[А-Яа-яA-Za-z]?")

# Окно, внутри которого вторая запись той же машины — это повторное нажатие,
# а не второй приезд.
#
# Меряется по ТОЙ ЖЕ машине, а не по потоку разных: сверка идёт по бортовому
# номеру, и две разные машины не столкнутся, как бы плотно они ни шли. Прежние
# 60 с брались от медианы интервала прибытий (80 с) — величина не та, и край
# окна пришёлся ровно на живой случай: борт 38427, строки 527 и 537, два
# нажатия с разницей 63 с и исправленной наполненностью.
#
# Замер по суткам на 22739 (после починки полей): 51 пара одного борта лежит в
# пределах 63 с, следующая — через ДВА ЧАСА. Пять минут стоят внутри этой
# пустой полосы с запасом в обе стороны.
DUPLICATE_WINDOW_S = 300.0

# Разрыв, с которого начинается новая смена. Съёмка идёт тремя окнами с
# перерывами в часы, а внутри окна машины идут каждые полторы минуты.
SHIFT_GAP_S = 3600.0

# Колонки выгрузки. Имена заголовков не проверяем — важен порядок, а он у
# формы фиксирован.
_COLUMNS = {"created": "A", "stop": "B", "kind": "C", "route": "D",
            "size": "E", "board": "F", "occupancy": "G"}


@dataclass(frozen=True)
class OperatorRecord:
    """Одно нажатие кнопки. `row` — номер строки выгрузки, чтобы на неё указать."""

    created: datetime
    stop: str
    kind: str
    route: str
    size: str
    board: str
    occupancy: str | None
    row: int

    @property
    def well_formed(self) -> bool:
        """Похожи ли поля на самих себя. Это про форму, а не про правду."""
        return bool(BOARD_RE.fullmatch(self.board) and ROUTE_RE.fullmatch(self.route))


def read_export(path: Path) -> list[OperatorRecord]:
    """Читает выгрузку целиком, без отбора и без починки."""
    records: list[OperatorRecord] = []
    for number, cell in enumerate(sheet_cells(path), start=1):
        if number == 1:
            continue  # шапка
        created = _moment(cell.get(_COLUMNS["created"], ""))
        if created is None:
            continue
        records.append(
            OperatorRecord(
                created=created,
                stop=(cell.get(_COLUMNS["stop"]) or "").strip(),
                kind=(cell.get(_COLUMNS["kind"]) or "").strip(),
                route=(cell.get(_COLUMNS["route"]) or "").strip(),
                size=(cell.get(_COLUMNS["size"]) or "").strip(),
                board=(cell.get(_COLUMNS["board"]) or "").strip(),
                occupancy=(cell.get(_COLUMNS["occupancy"]) or "").strip() or None,
                row=number,
            )
        )
    return sorted(records, key=lambda r: r.created)


def for_stop(records: list[OperatorRecord], stop: str,
             day: Date | None = None) -> list[OperatorRecord]:
    """Записи одной остановки за один день — в выгрузке их 23 штуки разом."""
    return [
        r for r in records
        if r.stop == stop and (day is None or r.created.date() == day)
    ]


def repair(record: OperatorRecord) -> tuple[OperatorRecord, str | None]:
    """Чинит однозначно читаемую порчу. Неоднозначную — оставляет видимой.

    Возвращает запись и пояснение, что именно сделано; пояснение уходит в
    `DeliveryRow.overrides`, чтобы спор о строке можно было разобрать.
    """
    if record.well_formed:
        return record, None

    # Поля переставлены местами: каждое подходит другому и не подходит своему.
    if BOARD_RE.fullmatch(record.route) and ROUTE_RE.fullmatch(record.board):
        return (
            replace(record, board=record.route, route=record.board),
            f"борт и маршрут поменяны местами (было борт {record.board}, "
            f"маршрут {record.route})",
        )

    # В маршрут вписан ещё и бортовой номер: «501596» при борте «1596».
    if record.route.endswith(record.board):
        head = record.route[: -len(record.board)]
        if ROUTE_RE.fullmatch(head):
            return (
                replace(record, route=head),
                f"из маршрута снята склейка с бортовым (было {record.route})",
            )

    return record, None


@dataclass(frozen=True)
class Dedup:
    """Итог снятия дублей: что оставлено, что снято и о чём оператор спорил."""

    kept: list[OperatorRecord]
    dropped: list[OperatorRecord]
    # Пары, где повторное нажатие пришло с другим маршрутом: оператор сам себя
    # поправил. Какая из версий верна, решает не этот модуль — маршрут виден на
    # табло и известен порталу, а здесь важно не потерять сам факт сомнения.
    disputed: list[tuple[OperatorRecord, OperatorRecord]]


@dataclass(frozen=True)
class Correction:
    """Запись с исправленным маршрутом и то, что стояло в ней прежде."""

    record: OperatorRecord
    was: str


def with_last_route(
    records: list[OperatorRecord],
    disputed: list[tuple[OperatorRecord, OperatorRecord]],
) -> tuple[list[OperatorRecord], list[Correction]]:
    """Маршрут по ПОСЛЕДНЕМУ нажатию: перенажатие с другим маршрутом — поправка.

    Оператор жмёт кнопку второй раз не только по ошибке. В четырёх случаях
    боевой выгрузки второе нажатие пришло через 4-25 секунд с другим маршрутом:
    он сам себя поправил. Прежде главной оставалась первая запись, и в книгу
    уходил ровно тот маршрут, который он и исправлял, — а исправление стояло
    рядом, помеченное розовым как лишняя строка.

    Портал на стороне последнего нажатия: борт 38140 в этот день работал на
    64, а первая запись говорила 62. Решение заказчика 20.09.

    Время берётся от ПЕРВОГО нажатия и не трогается: оно ближе к моменту
    прибытия, а из него считаются графы C и D. Второе нажатие остаётся в
    списке как есть — из книги повторы не исчезают (решение 075).
    """
    по_первому = {id(first): second.route for first, second in disputed}
    out: list[OperatorRecord] = []
    fixed: list[Correction] = []
    for record in records:
        route = по_первому.get(id(record))
        if route is None or route == record.route:
            out.append(record)
            continue
        corrected = replace(record, route=route)
        out.append(corrected)
        fixed.append(Correction(record=corrected, was=record.route))
    return out, fixed


def drop_duplicates(records: list[OperatorRecord]) -> Dedup:
    """Снимает повторные нажатия — по бортовому номеру, а не по паре с маршрутом.

    Сверка по паре «борт и маршрут» пропускала четыре случая из боевой
    выгрузки, где оператор нажал второй раз именно чтобы исправить маршрут
    (08:29:31 маршрут 62 и 08:29:35 маршрут 64 у борта 38140). Они выглядели
    как два разных визита, то есть давали лишнюю строку — ровно то, за что
    заказчик бракует файл.

    Снятые не выбрасываются: 50 строк из 292 — шестая часть файла, и решение
    об их судьбе должно быть предъявимым, а не молчаливым.
    """
    kept: list[OperatorRecord] = []
    dropped: list[OperatorRecord] = []
    disputed: list[tuple[OperatorRecord, OperatorRecord]] = []
    # Личность машины известна только ПОСЛЕ починки полей: оператор путает борт
    # с маршрутом, и по напечатанному две записи одной машины выглядят разными
    # бортами, а две разные машины с одним номером маршрута в графе борта —
    # одной. Обе ошибки тихие, поэтому сверка идёт по починенному номеру, а в
    # ответе остаются исходные записи: чинит их отдельный шаг.
    def identity(record: OperatorRecord) -> str:
        return repair(record)[0].board
    for record in sorted(records, key=lambda r: r.created):
        twin = next(
            (
                k for k in reversed(kept)
                if identity(k) == identity(record)
                and (record.created - k.created).total_seconds() <= DUPLICATE_WINDOW_S
            ),
            None,
        )
        if twin is None:
            kept.append(record)
            continue
        # Оставляем первое нажатие: оно ближе к моменту прибытия, а время
        # уходит в колонки C и D таблицы заказчика.
        dropped.append(record)
        if twin.route != record.route:
            disputed.append((twin, record))
    return Dedup(kept=kept, dropped=dropped, disputed=disputed)


def shifts(records: list[OperatorRecord]) -> list[list[OperatorRecord]]:
    """Режет записи на смены по длинному перерыву между ними."""
    ordered = sorted(records, key=lambda r: r.created)
    if not ordered:
        return []
    groups = [[ordered[0]]]
    for prev, cur in zip(ordered, ordered[1:]):
        if (cur.created - prev.created).total_seconds() > SHIFT_GAP_S:
            groups.append([cur])
        else:
            groups[-1].append(cur)
    return groups


def _moment(text: str) -> datetime | None:
    match = re.search(r"(\d{4})-(\d{2})-(\d{2})[ T](\d{2}):(\d{2}):(\d{2})", text or "")
    return datetime(*map(int, match.groups())) if match else None
