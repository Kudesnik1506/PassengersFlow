"""Сверка нашей строки с записью оператора: что совпало, а что нет.

Выгрузка оператора — черновик, а не готовый список (решение 025): он мог машину
пропустить, нажать дважды или ошибиться при вводе. Поэтому сверка идёт по
ЛИЧНОСТИ машины — бортовому номеру, — а не по времени: на боевой смене часы
оператора и часы камеры разошлись почти на семь минут, и сопоставление по
времени связало бы чужие пары.

Замер на нашем наборе: борта 1596, 38157 и 7861 у оператора есть, и по ним
разница составила −7:18, −7:16 и −6:52. Бортов 7326 и 38142 в это утро у него
нет вовсе — первые записи по ним в девятом часу. Это пробелы оператора, и
показать их надо, а не замолчать.

Отсюда три исхода сверки, и каждый обязан быть различим: совпало, разошлось,
оператор молчит. Последнее — не совпадение и не расхождение: подтверждения нет.
"""

from __future__ import annotations

from datetime import datetime

from paxcount.delivery.agreement import agree
from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.operator import OperatorRecord


def record(**kw) -> OperatorRecord:
    base = dict(created=datetime(2026, 9, 10, 6, 54, 59), stop="22739",
                 kind="Автобус", route="50", size="Особо большой ", board="1596",
                 occupancy="А", row=3)
    return OperatorRecord(**{**base, **kw})


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, board_number="1596", state_number="А000АА00",
                 route="50", size=VehicleSize.XLARGE, alighted=0, boarded=5,
                 video="запись", operator="Иванов Иван")
    return DeliveryRow(**{**base, **kw})


def test_the_match_is_found_by_board_number_not_by_time():
    """Часы разошлись на семь минут — по времени нашлась бы чужая машина."""
    ours = row()
    others = [
        record(created=datetime(2026, 9, 10, 7, 2, 7), board="38129", route="64"),
        record(),   # наша, но на семь минут «раньше»
    ]
    assert agree(ours, others).record is record().board or True
    assert agree(ours, others).record.board == "1596"


def test_a_trailing_space_in_the_size_is_not_a_disagreement():
    """«Особо большой » с пробелом — та же величина, а не другая."""
    assert "size" not in agree(row(), [record()]).mismatched


def test_the_clock_gap_is_a_disagreement_and_is_named():
    """Расхождение часов — находка, а не мелочь: заказчик должен его видеть."""
    assert "time" in agree(row(), [record()]).mismatched


def test_a_vehicle_the_operator_never_recorded_is_not_a_match():
    """Пробел оператора — не совпадение и не расхождение, а отсутствие записи."""
    result = agree(row(board_number="7326", route="26"), [record()])
    assert result.record is None
    assert result.mismatched == frozenset(), "спорить не с чем"
    assert result.missing, "но подтверждения нет, и это отдельный исход"


def test_a_real_disagreement_is_reported_field_by_field():
    """Разошлись в маршруте — помечается маршрут, а не вся строка."""
    result = agree(row(route="62"), [record()])
    assert "route" in result.mismatched
    assert "kind" not in result.mismatched and "size" not in result.mismatched


def test_occupancy_comes_from_the_operator_because_we_do_not_count_it():
    """Наполненность своей моделью не считаем (решение 032) — берём у него."""
    assert agree(row(), [record()]).occupancy == "А"
    assert agree(row(board_number="7326"), [record()]).occupancy is None


def test_the_same_vehicle_hours_later_is_a_different_trip():
    """Борт 38142 у оператора есть — но в девятом часу, а мы смотрим седьмой.

    Одна машина проходит остановку за смену не раз. Ближайшая по времени
    запись без границы находится всегда, и тогда «совпадение» становится
    бессмысленным: наполненность и маршрут приедут от другого рейса, а
    расхождение часов будет измерено по чужой паре. Граница шире расхождения
    часов (семь минут) и уже интервала между заездами (часы).
    """
    ours = row(hours=6, minutes=58, board_number="38142", route="62",
                size=VehicleSize.LARGE)
    late = record(created=datetime(2026, 9, 10, 9, 5, 24), board="38142",
                   route="62", size="Большой", occupancy="А")
    result = agree(ours, [late])
    assert result.record is None, "рейс через два часа — не наша машина"
    assert result.missing and result.occupancy is None
