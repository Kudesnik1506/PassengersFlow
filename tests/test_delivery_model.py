"""Строка таблицы заказчика: что в неё попадает и что она запрещает.

Две вещи, которых нет в самой таблице заказчика и которые модель обязана
держать сама.

Первое — оба номера рядом (решение 026). В принятой таблице бортовой номер
автобуса затёрт государственным, и обратного хода нет: портал по госномеру не
ищет. Строка, потерявшая бортовой, ничем не перепроверяется, а платят за файл
по итогам чужой проверки.

Второе — различие «ноль» и «не наблюдалось». Ноль вошедших — это результат
счёта, N/A — отказ от счёта по правилу инструкции (ТС уже стояло с открытыми
дверями в начале съёмки). Свести их в одно число значит соврать заказчику
дважды: выдать несчитанное за посчитанное и потерять повод для проверки.
"""

from __future__ import annotations

import pytest
from paxcount.delivery.model import (
    DeliveryRow,
    Occupancy,
    VehicleKind,
    VehicleSize,
)
from pydantic import ValidationError


def row(**over) -> DeliveryRow:
    base = dict(
        group="1317", date="2026-05-19", hours=7, minutes=0, stop="1715-15182",
        kind=VehicleKind.BUS, board_number="38099", state_number="Р512ОЕ198",
        route="481", occupancy=Occupancy.A, size=VehicleSize.LARGE,
        alighted=3, boarded=2,
        video="2026-05-19 - 06-59-54 - 1715-15182 - 01", operator="Иванов Иван",
    )
    base.update(over)
    return DeliveryRow(**base)


# ---- Колонка G: номер зависит от вида ТС -----------------------------------
#
# Инструкция: «Для троллейбусов и трамваев указывается бортовой номер, а для
# автобусов государственный». Замена делается только автобусам, и только у них
# она вообще имеет смысл: у троллейбуса госномера нет — портал на запрос по
# борту 3144 возвращает в графе госномера те же 3144.


def test_bus_reports_state_number():
    assert row().number == "Р512ОЕ198"


def test_trolley_reports_board_number():
    r = row(kind=VehicleKind.TROLLEY, board_number="3144", state_number=None)
    assert r.number == "3144"


def test_tram_reports_board_number():
    r = row(kind=VehicleKind.TRAM, board_number="5012", state_number=None)
    assert r.number == "5012"


def test_bus_without_state_number_falls_back_to_board():
    """Графа зовётся «Бортовой ИЛИ государственный», и пустой она быть не должна.

    Раньше здесь стоял `None`: «госномера пишем обязательно», и подстановка
    борта выглядела бы как выполненное требование. Принятый заказчиком файл это
    опроверг — графа заполнена во всех 312 строках, и у восьми автобусов в ней
    бортовой номер («7364 - нет в базе», «7417»). Пустая графа не строже, а
    просто беднее: заказчик теряет единственный ключ, по которому машину можно
    опознать в справочнике.
    """
    r = row(state_number=None)
    assert r.number == "38099"


def test_the_fallback_does_not_hide_the_missing_state_number():
    """Подстановка не выдаёт себя за успех: приёмка по-прежнему жалуется.

    Опасение, ради которого графу оставляли пустой, снимается здесь: правило
    приёмки смотрит на `state_number`, а не на то, что напечатано в графе.
    """
    from paxcount.delivery.validate import validate
    problems = validate([row(state_number=None)])
    assert any(p.field == "state_number" for p in problems)


def test_board_number_survives_replacement():
    """Решение 026: замена не должна быть необратимой, как у заказчика."""
    r = row()
    assert r.board_number == "38099"
    assert r.number == "Р512ОЕ198"


# ---- Ноль против N/A -------------------------------------------------------


def test_counts_may_be_absent_and_that_is_not_zero():
    r = row(alighted=None, boarded=None)
    assert r.alighted is None and r.boarded is None
    assert r.counted is False


def test_zero_is_a_counted_result():
    r = row(alighted=0, boarded=0)
    assert r.counted is True


def test_negative_counts_rejected():
    with pytest.raises(ValidationError):
        row(boarded=-1)


# ---- Словари заказчика закрыты --------------------------------------------


def test_unknown_vehicle_kind_rejected():
    with pytest.raises(ValidationError):
        row(kind="Дилижанс")


def test_unknown_occupancy_rejected():
    """Наполненность — буква А..Е, латинская «A» сюда не проходит."""
    with pytest.raises(ValidationError):
        row(occupancy="A")


def test_all_six_occupancy_codes_exist():
    assert [o.value for o in Occupancy] == ["А", "Б", "В", "Г", "Д", "Е"]


def test_tram_sizes_are_wagons():
    assert VehicleSize.ONE_CAR.value == "Один вагон"
    assert VehicleSize.THREE_CARS.value == "Три вагона"


# ---- Комментарий: только коды из таблицы 2 ---------------------------------


@pytest.mark.parametrize("code", [1, 4, 8])
def test_comment_codes_accepted(code):
    assert row(comment=code).comment == code


@pytest.mark.parametrize("code", [0, 9, 42])
def test_comment_codes_outside_table_rejected(code):
    with pytest.raises(ValidationError):
        row(comment=code)


def test_comment_is_optional():
    assert row().comment is None


# ---- Время ------------------------------------------------------------------


def test_hours_and_minutes_bounded():
    with pytest.raises(ValidationError):
        row(hours=24)
    with pytest.raises(ValidationError):
        row(minutes=60)


# ---- Происхождение значения: чьё оно ---------------------------------------
#
# Инструкция разрешает исправлять данные оператора, но платят за файл по итогам
# чужой проверки: заменив верное своим ошибочным, мы создадим ошибку, которой
# без нас не было бы. Значит заменённое поле обязано быть видно — иначе
# разобрать потом, чья это ошибка, нечем.


def test_row_records_which_fields_we_overrode():
    r = row(overrides={"occupancy": "оператор: Б, по кадрам Г"})
    assert r.overrides["occupancy"].startswith("оператор")


def test_overrides_empty_by_default():
    assert row().overrides == {}
