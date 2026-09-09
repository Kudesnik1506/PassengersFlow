"""Дефект 2: двойной счёт на видео 04.

Один поезд получил два id трекера (111 и 353), визиты по обоим пересекались во
времени и стояли в одном месте кадра. Треки 349/305/377 попали в оба визита —
один человек посчитан дважды.

Ключевая деталь, из-за которой первая версия склейки не сработала: у двух рамок
на ОДНОМ объекте IoU проседает (замер на 04: 0.45), потому что детектор
систематически обрезает объект, а не раздувает его. Поэтому мерой служит
пересечение к площади МЕНЬШЕЙ рамки, а не IoU.
"""

from __future__ import annotations

import numpy as np
from conftest import box, make_vehicle_track, make_visit
from paxcount.visits import _overlap_min, merge_fragments

FRAMES = list(range(0, 60))


def iou(a: np.ndarray, b: np.ndarray) -> float:
    """Только для теста: показать, почему IoU здесь не годится."""
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return float(inter / (area_a + area_b - inter))


def scene(boxes_by_track: dict[int, np.ndarray], windows: dict[int, tuple[float, float]]):
    """Сцена: у каждого трека своя рамка, постоянная на всех кадрах."""
    raw = {tid: {f: b for f in FRAMES} for tid, b in boxes_by_track.items()}
    found = [
        (
            make_vehicle_track(tid, raw[tid]),
            make_visit(arrival_ts=lo, departure_ts=hi, visit_id=tid, vehicle_track_id=tid),
        )
        for tid, (lo, hi) in windows.items()
    ]
    return found, raw


def test_two_ids_on_one_vehicle_are_merged():
    """Обрезанные с разных сторон рамки одного поезда — это один визит."""
    left = box(100, 100, 500, 400)
    right = box(250, 100, 650, 400)

    # Ровно тот случай, ради которого выбрана мера: IoU ниже любого разумного
    # порога, а пересечение к меньшей площади — выше.
    assert iou(left, right) < 0.5
    assert _overlap_min(left, right) >= 0.6

    found, raw = scene({111: left, 353: right}, {111: (0.0, 2.0), 353: (0.5, 2.5)})
    kept = merge_fragments(found, raw)

    assert len(kept) == 1
    visit = kept[0][1]
    assert (visit.arrival_ts, visit.departure_ts) == (0.0, 2.5)


def test_car_inside_the_bus_box_is_not_merged():
    """Защита по площади обязательна.

    Легковушка, целиком попавшая в рамку автобуса, даёт `_overlap_min` = 1.0 —
    без сравнения площадей она слилась бы с автобусом, и её пассажиры ушли бы
    в чужой визит.
    """
    bus = box(350, 200, 900, 600)
    car = box(400, 250, 470, 350)
    assert _overlap_min(bus, car) == 1.0

    found, raw = scene({1: bus, 2: car}, {1: (0.0, 5.0), 2: (0.0, 5.0)})
    assert len(merge_fragments(found, raw)) == 2


def test_two_vehicles_apart_are_not_merged():
    found, raw = scene(
        {1: box(100, 100, 500, 400), 2: box(900, 100, 1300, 400)},
        {1: (0.0, 5.0), 2: (0.0, 5.0)},
    )
    assert len(merge_fragments(found, raw)) == 2


def test_same_place_but_different_time_is_not_merged():
    """Два автобуса подряд на одном месте — два визита, а не один.

    Это основной сценарий многочасовой записи: рамки почти совпадают, но
    развести их обязано время.
    """
    same = box(100, 100, 500, 400)
    found, raw = scene({1: same, 2: same}, {1: (0.0, 5.0), 2: (6.0, 11.0)})
    assert len(merge_fragments(found, raw)) == 2


def test_merged_visit_keeps_the_longer_track_as_base():
    """База — более длинный трек: по нему считаются канонические окна."""
    long_boxes = {f: box(100, 100, 500, 400) for f in FRAMES}
    short_boxes = {f: box(250, 100, 650, 400) for f in FRAMES[:10]}
    raw = {111: long_boxes, 353: short_boxes}
    found = [
        (make_vehicle_track(353, short_boxes), make_visit(0.5, 2.5, visit_id=353,
                                                          vehicle_track_id=353)),
        (make_vehicle_track(111, long_boxes), make_visit(0.0, 2.0, visit_id=111,
                                                         vehicle_track_id=111)),
    ]
    kept = merge_fragments(found, raw)
    assert len(kept) == 1
    assert kept[0][0].track_id == 111
