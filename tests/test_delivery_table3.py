"""Таблица 3 инструкции: какие пары «тип ТС + размер» существуют, а какие нет.

Разобрана по ячейкам исходного `.docx`, а не по плоскому тексту: в плоском виде
подписи «Таких нет» и «Таких нет в СПб» отрываются от своих клеток и пары
приходится угадывать.

| Тип | что таблица 3 разрешает |
|---|---|
| Автобус | Малый (одна дверь, ГАЗель), Средний (2), Большой (3), Особо большой (гармошка, 4) |
| Трамвай | Один вагон, Два вагона, Три вагона |
| Троллейбус | Большой (трёхдверный), Особо большой (с гармошкой). Малый — «Таких нет», Средний — «Таких нет в СПб» |
| Маршрутка | Малый, Средний. Большой и Особо большой — «Таких нет в СПб» |

Зачем это в коде. Оператор жмёт кнопку на остановке и заполняет обе графы на
глаз; пара, которой в Петербурге не существует, — признак описки, а не редкой
машины. Сами графы мы не правим: кто ошибся, отсюда не видно, — расхождение
уходит в графу S (решение 087).

Про «Очень большой»: инструкция в строке автобуса пишет так, а в строках
троллейбуса и маршрутки — «Особо большой». Выпадающий список шаблона знает
только «Особо большой», и он здесь источник истины: в книгу уходит то, что
примет проверка заказчика.

«Развозка» и «Шаттл» в таблице 3 не описаны вовсе — это назначение рейса, а не
кузов. Читаем их по строке автобуса, и это наше прочтение, а не буква
инструкции.
"""

from __future__ import annotations

import pytest

from paxcount.delivery.model import VehicleKind, VehicleSize
from paxcount.delivery.table3 import SIZES_BY_KIND, kinds_for_size, pair_note

BUS, TROLLEY, TRAM = VehicleKind.BUS, VehicleKind.TROLLEY, VehicleKind.TRAM
MINIBUS = VehicleKind.MINIBUS
SMALL, MEDIUM = VehicleSize.SMALL, VehicleSize.MEDIUM
LARGE, XLARGE = VehicleSize.LARGE, VehicleSize.XLARGE


@pytest.mark.parametrize("kind,size", [
    (BUS, SMALL), (BUS, MEDIUM), (BUS, LARGE), (BUS, XLARGE),
    (TRAM, VehicleSize.ONE_CAR), (TRAM, VehicleSize.TWO_CARS),
    (TRAM, VehicleSize.THREE_CARS),
    (TROLLEY, LARGE), (TROLLEY, XLARGE),
    (MINIBUS, SMALL), (MINIBUS, MEDIUM),
])
def test_a_pair_the_table_allows_is_silent(kind, size):
    assert pair_note(kind, size) is None


def test_a_small_trolley_does_not_exist():
    """«Таких нет» — не редкость, а описка в одной из двух граф."""
    note = pair_note(TROLLEY, SMALL)
    assert note and "Троллейбус" in note and "Малый" in note


def test_a_large_minibus_does_not_exist_in_the_city():
    note = pair_note(MINIBUS, LARGE)
    assert note and "Маршрутка" in note


def test_a_bus_is_not_measured_in_cars():
    """Вагоны — мера рельсового транспорта, у автобуса их не бывает."""
    assert pair_note(BUS, VehicleSize.TWO_CARS) is not None


def test_a_tram_is_measured_in_cars_only():
    assert pair_note(TRAM, LARGE) is not None


def test_a_shuttle_is_read_by_the_bus_row():
    """Развозка и шаттл — назначение рейса, кузов у них автобусный."""
    assert pair_note(VehicleKind.SHUTTLE_WORK, LARGE) is None
    assert pair_note(VehicleKind.SHUTTLE_MALL, VehicleSize.ONE_CAR) is not None


def test_an_unknown_half_of_the_pair_is_not_judged():
    """Нет одной из граф — сравнивать нечего, а догадка тут хуже молчания."""
    assert pair_note(None, LARGE) is None
    assert pair_note(BUS, None) is None


def test_the_size_narrows_the_kind():
    """Обратная сторона таблицы: три двери — не маршрутка и не трамвай."""
    assert kinds_for_size(LARGE) == frozenset(
        {BUS, TROLLEY, VehicleKind.SHUTTLE_WORK, VehicleKind.SHUTTLE_MALL})
    assert kinds_for_size(VehicleSize.ONE_CAR) == frozenset({TRAM})


def test_every_kind_of_the_dropdown_has_a_row():
    """Вид из списка шаблона без строки таблицы 3 молча пропускал бы проверку."""
    assert set(SIZES_BY_KIND) == set(VehicleKind)
