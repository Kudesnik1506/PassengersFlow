"""Сборка разметки дверей из автопредложения: известное K — главный арбитр.

`doorprop` умеет предлагать проёмы двумя методами, но не знает, сколько их
должно быть. Число дверей известно из размера ТС (таблица 3 инструкции), и это
самая надёжная величина во всей задаче: она приходит из справочника, а не из
пикселей. Поэтому она и работает арбитром — выбирает ровно K кандидатов и
называет недостающие.

Главная честность модуля — в различении двух непохожих случаев, которые
выглядят одинаково как «нашли меньше, чем ожидали»:

* корпус ТС обрезан краем кадра, и недостающая дверь физически за кадром —
  это законный `in_frame=False`, из него потом растёт код 1-8 в таблице
  заказчика;
* корпус виден целиком, а дверь всё равно не нашлась — это промах детектора,
  а не дверь за кадром. Пометить её `in_frame=False` значило бы выдать свой
  промах за свойство съёмки: в отчёт уйдёт код «дверь не видна», недосчёт
  станет законным, и никто никогда не узнает, что дверь была в кадре.
"""

from __future__ import annotations

import pytest
from paxcount.delivery.model import VehicleSize
from paxcount.doorprop import DoorCandidate
from paxcount.doorprop.layout import layout_from_candidates

# Кузов занимает середину кадра 1920x1080 — краёв не касается.
BOX = (300.0, 400.0, 1500.0, 800.0)
FRAME = (1920, 1080)


def candidate(x0: float, x1: float, score: float = 0.5) -> DoorCandidate:
    return DoorCandidate(x0=x0, y0=0.3, x1=x1, y1=0.95, score=score, method="test")


def build(cands, **over):
    base = dict(
        candidates=cands, size=VehicleSize.LARGE, visit_key="2/07:02:06/1596",
        camera="2", orientation="нос-слева", box_px=BOX, frame_size=FRAME,
        video="2026-09-10 - 06-56-06 - 22739_2 - 02", frame_idx=7326,
    )
    base.update(over)
    return layout_from_candidates(**base)


# ---- Полное совпадение ------------------------------------------------------


def test_three_candidates_for_a_large_bus_become_three_doors():
    layout, problems = build([candidate(0.1, 0.2), candidate(0.4, 0.5), candidate(0.7, 0.8)])
    assert problems == []
    assert [d.n_from_nose for d in layout.doors] == [1, 2, 3]
    assert all(d.in_frame for d in layout.doors)


def test_door_pixels_are_absolute_frame_coordinates():
    """Разметка живёт в пикселях кадра: по ней режется кроп, а не доля bbox."""
    layout, _ = build([candidate(0.0, 0.1), candidate(0.4, 0.5), candidate(0.9, 1.0)])
    first = layout.doors[0]
    assert first.opening_px is not None
    assert first.opening_px[0] == pytest.approx(300.0)  # x0 bbox + 0.0 * ширина
    assert first.opening_px[2] == pytest.approx(420.0)  # + 0.1 * 1200


def test_nose_on_the_right_reverses_numbering():
    """Нумерация — от носа, а не слева направо: у встречного ракурса они обратны."""
    layout, _ = build(
        [candidate(0.1, 0.2), candidate(0.4, 0.5), candidate(0.7, 0.8)],
        orientation="нос-справа",
    )
    # Самый правый кандидат ближе к носу — он и первый.
    numbers = [(d.n_from_nose, d.opening_px[0]) for d in layout.doors]
    assert numbers[0][0] == 1
    assert numbers[0][1] > numbers[-1][1], "первая дверь правее последней"


# ---- Дверь за краем кадра — законный случай ---------------------------------


def test_missing_door_at_a_truncated_edge_is_out_of_frame():
    """Корпус упирается в левый край, нашли две двери из трёх — третья за кадром."""
    truncated = (0.0, 400.0, 1200.0, 800.0)
    layout, problems = build(
        [candidate(0.3, 0.4), candidate(0.7, 0.8)], box_px=truncated,
    )
    assert problems == [], "за кадром — это не проблема разметки, а свойство съёмки"
    missing = [d for d in layout.doors if not d.in_frame]
    assert [d.n_from_nose for d in missing] == [1], "нос слева, слева же и обрезано"
    assert missing[0].opening_px is None, "координат у невидимой двери нет"


def test_truncation_on_the_tail_side_blames_the_last_door():
    truncated_right = (300.0, 400.0, 1920.0, 800.0)
    layout, problems = build(
        [candidate(0.1, 0.2), candidate(0.5, 0.6)], box_px=truncated_right,
    )
    assert problems == []
    assert [d.n_from_nose for d in layout.doors if not d.in_frame] == [3]


# ---- Промах детектора — НЕ выдаём за дверь за кадром ------------------------


def test_missing_door_without_truncation_is_a_problem_not_an_absent_door():
    """Кузов виден целиком, а дверь не нашлась — это промах, а не съёмка.

    Молча поставить `in_frame=False` значило бы превратить свой промах в
    законный код «дверь не видна» и узаконить недосчёт.
    """
    layout, problems = build([candidate(0.1, 0.2), candidate(0.5, 0.6)])
    assert problems, "о недостаче должно быть сказано вслух"
    assert "3" in problems[0] and "2" in problems[0]
    assert layout is None or any("не найден" in p for p in problems)


def test_extra_candidates_are_trimmed_to_the_known_count():
    """Четыре кандидата на трёхдверный автобус — одно из окон принято за дверь."""
    layout, problems = build([
        candidate(0.1, 0.2, score=0.9), candidate(0.3, 0.4, score=0.2),
        candidate(0.5, 0.6, score=0.8), candidate(0.8, 0.9, score=0.7),
    ])
    assert layout is not None
    assert len(layout.doors) == 3
    assert problems and any("лишн" in p.lower() for p in problems)


def test_kept_candidates_are_the_highest_scoring_ones():
    layout, _ = build([
        candidate(0.1, 0.2, score=0.9), candidate(0.3, 0.4, score=0.1),
        candidate(0.5, 0.6, score=0.8), candidate(0.8, 0.9, score=0.7),
    ])
    lefts = [d.opening_px[0] for d in layout.doors]
    assert 300.0 + 0.3 * 1200 not in lefts, "самый слабый кандидат отброшен"


# ---- Рельсовый транспорт ----------------------------------------------------


def test_rail_size_has_no_known_door_count():
    """У вагонов размер — про гармошки, а не про двери: арбитру нечем судить."""
    layout, problems = build([candidate(0.2, 0.3)], size=VehicleSize.TWO_CARS)
    assert problems and any("неизвест" in p.lower() for p in problems)


# ---- Результат проходит тот же гейт, что и ручная разметка ------------------


def test_result_passes_the_manual_arithmetic_gate():
    from paxcount.truth import check_door_arithmetic

    layout, _ = build([candidate(0.1, 0.2), candidate(0.4, 0.5), candidate(0.7, 0.8)])
    assert check_door_arithmetic(layout) == []
