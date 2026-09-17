"""Границы признака выхода: что им засчитывается, а что нет.

Выход — появление в проёме и шаг НАРУЗУ, вниз по кадру. Ослабление до
«удаления от проёма в любую сторону» здесь пробовали и отменили: оно добавило
один настоящий выход и тут же дало ложный, потому что прохожий вдоль борта и
вышедший вдоль борта геометрически неразличимы (решение 055).

Три случая, которые набор держит: шаг к камере — выход; уход ВВЕРХ, в салон, —
вход, а не выход и не «нет события» (решение 053); топтание у двери — не
событие вовсе.
"""

from __future__ import annotations

import numpy as np
from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.core.types import Direction, DoorSpec, VehicleVisit, VideoConfig
from paxcount.zonecount import count_zone

FPS = 10.0
BODY = (100.0, 300.0, 900.0, 800.0)
ZONE = (500.0, 625.0, 600.0, 860.0)
VIDEO = "запись"


def spec() -> DoorSpec:
    return DoorSpec(door_id="д1", mode="zone", frame="absolute",
                     line_start={"x": ZONE[0], "y": ZONE[1]},
                     line_end={"x": ZONE[2], "y": ZONE[3]}, zone=ZONE)


def count(points: list[tuple[float, float]], first_frame: int = 15):
    """Человек появляется в проёме на `first_frame` и идёт по заданным точкам."""
    track = {first_frame + i: (x - 25.0, y - 100.0, x + 25.0, y)
              for i, (x, y) in enumerate(points)}
    rows = []
    for i in range(81):
        box = track.get(i)
        rows.append(FrameTracks(
            frame_idx=i, ts=i / FPS,
            person_ids=np.array([7] if box else [], dtype=int),
            person_boxes=np.array([box] if box else [], dtype=float).reshape(-1, 4),
            person_conf=np.ones(1 if box else 0),
            vehicle_ids=np.array([1], dtype=int),
            vehicle_boxes=np.array([BODY], dtype=float), vehicle_names=["bus"],
        ))
    data = TrackData(video=VIDEO, width=1920, height=1080, fps=FPS, stride=1, frames=rows)
    visit = VehicleVisit(video=VIDEO, visit_id=1, vehicle_track_id=-1,
                          arrival_ts=0.5, departure_ts=8.0)
    boxes = {f.frame_idx: np.asarray(BODY, dtype=float) for f in data.frames}
    return count_zone(data, VideoConfig(video=VIDEO), [visit], {1: boxes}, {1: [spec()]})


def test_exit_towards_the_camera_still_counted():
    """Прежний случай не должен пропасть: шаг наружу, вниз по кадру."""
    points = [(550.0, 750.0 + 25.0 * i) for i in range(11)]
    assert [e.direction for e in count(points)] == [Direction.OUT]


def test_going_up_into_the_cabin_is_boarding_not_exit():
    """Вверх и внутрь рамки кузова — это салон: вход, а не выход."""
    points = [(550.0, 750.0 - 15.0 * i) for i in range(11)]   # 750 → 600, выше зоны
    assert [e.direction for e in count(points)] == [Direction.IN]


def test_loitering_at_the_door_is_not_an_event():
    """Потоптался у проёма и остался — ни входа, ни выхода."""
    points = [(550.0 + (i % 2) * 4.0, 750.0 - (i % 2) * 3.0) for i in range(11)]
    assert count(points) == []
