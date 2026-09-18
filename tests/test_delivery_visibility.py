"""Код таблицы 2 для строки книги: найти визит, взять его рамку, судить по ней.

Разметка есть на шесть визитов из трёхсот, а детекция — на всю смену. Значит
код можно поставить и там, где разметки нет: `reconcile.clipped_code` судит по
обрезу кузова, а этот модуль отвечает на предыдущий вопрос — КАКОЙ визит
относится к строке.

Допуски здесь не подобраны на глаз, а замерены по 280 строкам К2: момент строки
лежит внутри визита в 149 случаях, а из остальных 86 стоят ПОСЛЕ него (медиана
18 с) против 45 перед (медиана 47 с). Асимметрия не случайна: оператор жмёт
кнопку, когда машина уже отходит.
"""

from __future__ import annotations

from datetime import datetime

from paxcount.delivery.visibility import Sighting, code_at
from paxcount.doorprop.layout import NOSE_RIGHT

FRAME = (1920, 1080)
NOON = datetime(2026, 9, 10, 12, 0, 0)


def sighting(start_s: int, end_s: int, box) -> Sighting:
    from datetime import timedelta

    return Sighting(start=NOON + timedelta(seconds=start_s),
                     end=NOON + timedelta(seconds=end_s),
                     box=box, frame_size=FRAME)


CUT_LEFT = (0.0, 200.0, 1500.0, 900.0)      # корма за кадром при носе справа
WHOLE = (300.0, 200.0, 1500.0, 900.0)


def at(seconds: int) -> datetime:
    from datetime import timedelta

    return NOON + timedelta(seconds=seconds)


def test_a_moment_inside_the_stop_takes_its_box():
    assert code_at(at(15), [sighting(10, 30, CUT_LEFT)], NOSE_RIGHT) == 7


def test_a_press_just_after_the_bus_left_still_belongs_to_it():
    """Медиана опоздания — 18 с: это та же машина, а не следующая."""
    assert code_at(at(45), [sighting(10, 30, CUT_LEFT)], NOSE_RIGHT) == 7


def test_a_press_long_after_belongs_to_nobody():
    """Машины идут через 80 с: на большом отрыве это уже чужая стоянка."""
    assert code_at(at(90), [sighting(10, 30, CUT_LEFT)], NOSE_RIGHT) is None


def test_a_moment_well_before_the_stop_is_not_matched():
    """Перед визитом машина ещё не встала — совпадения тут почти всегда чужие."""
    assert code_at(at(-40), [sighting(10, 30, CUT_LEFT)], NOSE_RIGHT) is None


def test_a_vehicle_fully_in_frame_gives_no_code():
    assert code_at(at(15), [sighting(10, 30, WHOLE)], NOSE_RIGHT) is None


def test_the_covering_sighting_wins_over_a_closer_edge():
    """Момент внутри одной стоянки не отдаётся соседней, чей край ближе."""
    sights = [sighting(10, 30, CUT_LEFT), sighting(31, 60, WHOLE)]
    assert code_at(at(29), sights, NOSE_RIGHT) == 7


def test_no_sightings_no_code():
    assert code_at(at(15), [], NOSE_RIGHT) is None
