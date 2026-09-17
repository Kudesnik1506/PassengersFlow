"""Лента смены: наши расшифровки, вписанные в последовательность оператора.

Инструкция расшифровщика говорит «расшифровываем весь материал», и бланк
заказчика устроен соответственно: строка на каждое ТС смены, строки по
времени. Наших расшифровок шесть, машин за утро больше сотни. Книга из шести
строк — не маленькая книга, а другая: она утверждает последовательность,
которой на остановке не было.

Поэтому лента собирается так: записи оператора — костяк, наши строки встают на
свои места внутри него, а машина, которую оператор пропустил, ВПИСЫВАЕТСЯ
между его записями. Строка, которую мы ещё не расшифровали, остаётся с пустым
счётом: `None` — «не считали», и до книги он доходит как N/A.

## Почему шкала одна и почему это шкала камеры

Часы камеры и часы оператора на боевой смене разошлись на семь минут (решение
068: −7:18, −7:16, −6:52 по трём машинам, найденным по борту). Лента,
собранная из двух шкал разом, переставит машины местами и останется при этом
правдоподобной на вид — худший вид ошибки.

Шкалой выбрано время камеры: «Ориентируемся на время на камере. А если оно
неверное или не отображается, то ориентируемся на выгрузку или бланк от
оператора» — инструкция расшифровщика. Записи оператора переводятся на неё
измеренной поправкой; наши строки не трогаются вовсе. Заодно время строки
сходится с именем файла в графе N: проверяющий, открыв запись на этой минуте,
увидит ту самую машину.

Поправка НЕ ПОДРАЗУМЕВАЕТСЯ равной нулю. Без неё слияние не делается: нулевая
поправка даёт ленту, не отличимую от правильной, где каждая наша строка стоит
между чужими соседями.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date as Date
from datetime import datetime, timedelta
from statistics import median

from .agreement import Agreement
from .model import DeliveryRow, Occupancy, VehicleKind, VehicleSize
from .operator import OperatorRecord, repair


@dataclass(frozen=True)
class Decoded:
    """Наша расшифровка: момент на шкале камеры, строка бланка и итог сверки."""

    moment: datetime
    row: DeliveryRow
    agreement: Agreement


@dataclass(frozen=True)
class Entry:
    """Одна машина ленты. `decoded` — расшифровывали ли её мы.

    `repaired` — графы, которые мы починили за оператором. Молчаливая правка
    чужих данных недопустима: заказчик сверяет книгу с выгрузкой, видит
    расхождение и не понимает, чьё оно. Поэтому починка называется поимённо и
    доходит до книги пометкой.
    """

    moment: datetime
    row: DeliveryRow
    decoded: bool
    record: OperatorRecord | None = None
    repaired: frozenset[str] = frozenset()


def clock_shift(decoded: list[Decoded]) -> timedelta | None:
    """Насколько часы камеры ушли вперёд от часов оператора.

    Медиана по машинам, найденным у оператора по бортовому номеру. Медиана, а
    не среднее: одна строка, испорченная при вводе, сдвинула бы среднее и
    утащила за собой всю ленту, а медиану — нет.

    `None` — ни одной общей машины: величину не из чего измерить, и это не
    повод считать её нулём.
    """
    deltas = [
        (d.moment - d.agreement.record.created).total_seconds()
        for d in decoded
        if d.agreement.record is not None
    ]
    return timedelta(seconds=median(deltas)) if deltas else None


def _enum(kind: type, text: str | None):
    """Значение словаря заказчика или `None`. Чужое слово догадкой не чиним."""
    try:
        return kind((text or "").strip())
    except ValueError:
        return None


def _row_from_record(
    record: OperatorRecord, shift: timedelta, *, group: str, stop: str,
    operator: str,
) -> tuple[DeliveryRow, frozenset[str]]:
    """Строка по одной записи оператора: всё, что он знает, и пустой счёт.

    Вторым значением — графы, починенные за оператором: их книга красит.

    Госномера у него нет — в выгрузке только бортовой, и в графу номера идёт
    он (решение 071). Поле `state_number` при этом остаётся пустым, и правило
    приёмки на него пожалуется: строка не расшифрована, и это видно.
    """
    fixed, why = repair(record)
    mended = frozenset(
        name for name in ("board", "route")
        if getattr(fixed, name) != getattr(record, name)
    )
    moment = fixed.created + shift
    row = DeliveryRow(
        group=group,
        date=moment.strftime("%d.%m.%Y"),
        hours=moment.hour,
        minutes=moment.minute,
        stop=stop,
        kind=_enum(VehicleKind, fixed.kind) or VehicleKind.BUS,
        board_number=fixed.board or None,
        state_number=None,
        route=fixed.route or None,
        occupancy=_enum(Occupancy, fixed.occupancy),
        size=_enum(VehicleSize, fixed.size),
        boarded=None,
        alighted=None,
        operator=operator,
        overrides={"операторская строка": why} if why else {},
    )
    return row, mended


def merge(
    decoded: list[Decoded],
    records: list[OperatorRecord],
    *,
    shift: timedelta | None,
    group: str,
    stop: str,
    operator: str,
) -> list[Entry]:
    """Лента смены: наши строки и записи оператора на одной шкале, по времени.

    Запись, которую наша строка уже нашла при сверке, второй строкой не
    становится: два рейса одного борта с разницей в семь минут — ровно та
    ошибка, за которую заказчик бракует файл.
    """
    if shift is None:
        raise ValueError(
            "поправка между часами камеры и часами оператора не измерена: "
            "без неё лента соберётся из двух шкал разом и переставит машины"
        )

    taken = {id(d.agreement.record) for d in decoded if d.agreement.record is not None}
    entries = [
        Entry(moment=d.moment, row=d.row, decoded=True, record=d.agreement.record)
        for d in decoded
    ]
    for r in records:
        if id(r) in taken:
            continue
        row, mended = _row_from_record(r, shift, group=group, stop=stop,
                                        operator=operator)
        entries.append(Entry(moment=r.created + shift, row=row, decoded=False,
                              record=r, repaired=mended))
    return sorted(entries, key=lambda e: e.moment)


def backbone(records: list[OperatorRecord], day: Date) -> list[OperatorRecord]:
    """Записи, из которых строится книга: ВЕСЬ день, а не одна смена.

    Съёмка идёт тремя окнами с перерывами в часы, и поначалу книга собиралась
    на то окно, где лежат наши расшифровки. Принятый заказчиком файл это
    опроверг: 312 строк за одну дату, часы с 07 до 20 — все три окна в одном
    файле. Инструкция говорит то же словами: «расшифровываем весь материал».

    Дата отсекается явно: выгрузка приходит за несколько дней разом, а файл
    заказчика называется датой и на неё же принимается.
    """
    return [r for r in records if r.created.date() == day]
