"""Какой визит относится к строке книги — по нему берётся рамка и кадр.

Разметка есть на шесть визитов из трёхсот, а детекция — на всю смену. Значит
судить о кадре можно и там, где разметки нет; этот модуль отвечает на первый
вопрос — КАКОЙ визит относится к строке. Что с ним делать дальше, знает
`edgedoors`.

Допуски здесь не подобраны на глаз, а замерены по 280 строкам К2: момент строки
лежит внутри визита в 149 случаях, а из остальных 86 стоят ПОСЛЕ него (медиана
18 с) против 45 перед (медиана 47 с). Асимметрия не случайна: оператор жмёт
кнопку, когда машина уже отходит.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from paxcount.delivery.visibility import Sighting, sighting_at

FRAME = (1920, 1080)
NOON = datetime(2026, 9, 10, 12, 0, 0)
BOX = (0.0, 200.0, 1500.0, 900.0)


def sighting(start_s: int, end_s: int) -> Sighting:
    return Sighting(start=NOON + timedelta(seconds=start_s),
                     end=NOON + timedelta(seconds=end_s),
                     box=BOX, frame_size=FRAME)


def at(seconds: int) -> datetime:
    return NOON + timedelta(seconds=seconds)


def test_a_moment_inside_the_stop_takes_that_visit():
    assert sighting_at(at(15), [sighting(10, 30)]) == sighting(10, 30)


def test_a_press_just_after_the_bus_left_still_belongs_to_it():
    """Медиана опоздания — 18 с: это та же машина, а не следующая."""
    assert sighting_at(at(45), [sighting(10, 30)]) == sighting(10, 30)


def test_a_press_long_after_belongs_to_nobody():
    """Машины идут через 80 с: на большом отрыве это уже чужая стоянка."""
    assert sighting_at(at(90), [sighting(10, 30)]) is None


def test_a_moment_well_before_the_stop_is_not_matched():
    """Перед визитом машина ещё не встала — совпадения тут почти всегда чужие."""
    assert sighting_at(at(-40), [sighting(10, 30)]) is None


def test_the_covering_sighting_wins_over_a_closer_edge():
    """Момент внутри одной стоянки не отдаётся соседней, чей край ближе."""
    sights = [sighting(10, 30), sighting(31, 60)]
    assert sighting_at(at(29), sights) == sighting(10, 30)


def test_no_sightings_no_visit():
    assert sighting_at(at(15), []) is None
