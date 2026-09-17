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
    """Одна машина ленты. `decoded` — расшифровывали ли её мы."""

    moment: datetime
    row: DeliveryRow
    decoded: bool
    record: OperatorRecord | None = None


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
) -> DeliveryRow:
    """Строка по одной записи оператора: всё, что он знает, и пустой счёт.

    Госномера у него нет — в выгрузке только бортовой, и подставлять бортовой
    в графу госномера нельзя (решение 026). Графа остаётся пустой, а правило
    приёмки на неё пожалуется: строка не расшифрована, и это видно.
    """
    fixed, _ = repair(record)
    moment = fixed.created + shift
    return DeliveryRow(
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
    )


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
    entries += [
        Entry(
            moment=r.created + shift,
            row=_row_from_record(r, shift, group=group, stop=stop, operator=operator),
            decoded=False,
            record=r,
        )
        for r in records
        if id(r) not in taken
    ]
    return sorted(entries, key=lambda e: e.moment)


def shift_around(shifts: list[list[OperatorRecord]],
                  moment: datetime) -> list[OperatorRecord]:
    """Смена оператора, накрывающая этот момент; иначе — ближайшая к нему.

    Книга собирается на одну смену, а не на сутки: между сменами часы стоят,
    и лента, склеенная через трёхчасовой перерыв, перестаёт быть
    последовательностью прибытий.
    """
    if not shifts:
        return []
    inside = [s for s in shifts if s[0].created <= moment <= s[-1].created]
    if inside:
        return inside[0]
    return min(shifts, key=lambda s: min(abs(s[0].created - moment),
                                          abs(s[-1].created - moment)))
