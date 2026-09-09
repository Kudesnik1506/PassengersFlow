"""Дефект 1: разметка видео 09 дала ноль событий.

Зоны были нарисованы по дверному ПРОЁМУ (низ 0.80–0.91 высоты кузова), а
считается положение НОГ: `_anchor` берёт низ-центр рамки человека. Замер по 09:
точки опоры лежат на 0.74–1.14 высоты кузова, медиана 0.97 — то есть ниже
нарисованных зон, местами ниже самого bbox. Прогон при этом не падал: он честно
считал ноль событий.

Здесь проверяется само свойство «зона накрывает полосу ног», а не конкретные
числа в конфигах — их проверяет гейт (см. test_zone_annotations.py).
"""

from __future__ import annotations

from conftest import BUS, zone_spec
from paxcount.core.types import DoorSpec, Point
from paxcount.doors import fallback_doors
from paxcount.settings import ZONE_MIN_BOTTOM, ZONE_MIN_TOP
from paxcount.zonecount import _inside

BUS_TOP, BUS_HEIGHT = BUS[1], BUS[3] - BUS[1]


def foot_at(fraction: float) -> tuple[float, float]:
    """Точка опоры на заданной доле высоты кузова, по центру дверей."""
    return (600.0, BUS_TOP + fraction * BUS_HEIGHT)


def test_zone_fractions_map_onto_vehicle_box():
    spec = zone_spec((0.0, 0.5, 1.0, 1.0))
    x0, y0, x1, y1 = spec.resolve_zone(BUS)
    assert (x0, x1) == (BUS[0], BUS[2])
    assert y0 == BUS_TOP + 0.5 * BUS_HEIGHT
    assert y1 == BUS[3]


def test_zone_bottom_below_one_extends_past_the_box():
    """Низ зоны > 1.0 обязан выходить НИЖЕ рамки ТС.

    Это не побочный эффект, а требование: ноги стоящего у двери человека
    оказываются ниже нижней границы bbox кузова, и без выхода за 1.0 их не
    поймать.
    """
    spec = zone_spec((0.0, 0.65, 1.0, 1.12))
    _, _, _, y1 = spec.resolve_zone(BUS)
    assert y1 > BUS[3]


def test_zone_drawn_on_the_doorway_misses_the_feet():
    """Регресс дефекта: зона по проёму не ловит медианную точку опоры."""
    doorway = zone_spec((0.4, 0.20, 0.6, 0.85))  # как было нарисовано на 09
    feet_band = zone_spec((0.4, 0.65, 0.6, 1.12))  # как стало после замера

    median_foot = foot_at(0.97)
    assert not _inside(median_foot, doorway.resolve_zone(BUS))
    assert _inside(median_foot, feet_band.resolve_zone(BUS))


def test_zone_top_excludes_passengers_seen_inside_the_saloon():
    """Второй край той же ошибки: слишком высокий верх зоны ловит салон.

    После переразметки 09 появилось ложное срабатывание — трек 96 оказался
    пассажиром ВНУТРИ автобуса, видимым сквозь дверной проём. Его точка опоры
    высоко (он стоит на полу салона, а пол выше земли), и порог по верху зоны
    отсекает такие треки.
    """
    correct = zone_spec((0.4, ZONE_MIN_TOP, 0.6, 1.12))
    inside_saloon = foot_at(0.45)
    assert not _inside(inside_saloon, correct.resolve_zone(BUS))
    assert _inside(foot_at(0.97), correct.resolve_zone(BUS))


def test_absolute_frame_ignores_the_vehicle_box():
    spec = zone_spec((10.0, 20.0, 30.0, 40.0), frame="absolute")
    assert spec.resolve_zone(BUS) == (10.0, 20.0, 30.0, 40.0)


def test_default_zone_satisfies_the_gate():
    """Значение по умолчанию обязано проходить собственную проверку проекта.

    До правки верх дефолтной зоны стоял на 0.33 — при пороге гейта 0.60. На
    размеченных роликах это не проявлялось: там зоны заданы руками. Но первый
    же ролик без разметки берёт фолбэк (`doors.fallback_doors`), а он создаёт
    дверь именно со значением по умолчанию. То есть дефект ждал ровно первую
    боевую запись.
    """
    spec = zone_spec((0.0, 0.65, 1.0, 1.12))  # то, что ожидается по умолчанию
    default = DoorSpec(line_start=Point(x=0.0, y=1.0), line_end=Point(x=1.0, y=1.0))

    assert default.zone == spec.zone
    assert default.zone[1] >= ZONE_MIN_TOP
    assert default.zone[3] >= ZONE_MIN_BOTTOM


def test_fallback_door_satisfies_the_gate():
    """Фолбэк — единственный путь, где значение по умолчанию доходит до счёта."""
    for door in fallback_doors():
        assert door.zone[1] >= ZONE_MIN_TOP
        assert door.zone[3] >= ZONE_MIN_BOTTOM


def test_default_zone_covers_the_feet_band_and_not_the_saloon():
    """Проверка по существу, а не по порогам: кого зона ловит на деле."""
    default = DoorSpec(line_start=Point(x=0.0, y=1.0), line_end=Point(x=1.0, y=1.0))
    zone = default.resolve_zone(BUS)

    assert _inside(foot_at(0.97), zone)  # медианная точка опоры вышедшего
    assert not _inside(foot_at(0.45), zone)  # пассажир в салоне сквозь дверь


def test_thresholds_describe_a_non_empty_band():
    """Пороги гейта обязаны задавать непустую полосу.

    Проверка дешёвая, но без неё опечатка в settings.py (перепутанные местами
    верх и низ) прошла бы молча: гейт продолжил бы работать, отвергая любую
    корректную разметку.
    """
    assert ZONE_MIN_TOP < ZONE_MIN_BOTTOM
