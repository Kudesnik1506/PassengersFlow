"""Окно визита ограничивает событие, а не историю трека.

Замер на боевых записях (`tools/count_why.py`, визиты 4 и 5): несколько треков
начинаются ровно на границе окна счёта. Это не совпадение — окно открывается,
когда рамка ТС совпала с размеченным кузовом, то есть когда машина УЖЕ встала,
а пассажиры к этому моменту стоят у двери. Их история обрезается границей, и
признак «родился в зоне двери» вычисляется по обрубку: человек, подошедший
снаружи, выглядит как возникший в проёме — то есть как вышедший.

Отсюда разделение: в окно обязано попасть СОБЫТИЕ (смерть трека для входа,
рождение для выхода), а история трека берётся целиком. Трек — это улика, по
которой судят о направлении, и резать её границей визита значит судить по
половине улики.

Два теста ниже держат обе половины правила: первый требует полной истории,
второй — чтобы вместе с ней не начали засчитываться события чужого времени.
"""

from __future__ import annotations

import numpy as np
import pytest
from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.core.types import Direction, DoorSpec, VehicleVisit, VideoConfig
from paxcount.zonecount import count_zone

FPS = 10.0
BODY = (100.0, 300.0, 900.0, 800.0)
ZONE = (500.0, 625.0, 600.0, 860.0)   # полоса ног одной двери
VIDEO = "запись"


def spec() -> DoorSpec:
    return DoorSpec(door_id="д1", mode="zone", frame="absolute",
                     line_start={"x": ZONE[0], "y": ZONE[1]},
                     line_end={"x": ZONE[2], "y": ZONE[3]}, zone=ZONE)


def walker(first: int, last: int, y_from: float, y_to: float):
    """Человек, идущий к двери: точка опоры едет вверх кадра."""
    steps = max(last - first, 1)
    return {
        i: (550.0 - 25.0, y_from + (y_to - y_from) * (i - first) / steps - 100.0,
             550.0 + 25.0, y_from + (y_to - y_from) * (i - first) / steps)
        for i in range(first, last + 1)
    }


def data_with(track: dict[int, tuple], frames: int = 81) -> TrackData:
    rows = []
    for i in range(frames):
        box = track.get(i)
        rows.append(FrameTracks(
            frame_idx=i, ts=i / FPS,
            person_ids=np.array([7] if box else [], dtype=int),
            person_boxes=np.array([box] if box else [], dtype=float).reshape(-1, 4),
            person_conf=np.ones(1 if box else 0),
            vehicle_ids=np.array([1], dtype=int),
            vehicle_boxes=np.array([BODY], dtype=float),
            vehicle_names=["bus"],
        ))
    return TrackData(video=VIDEO, width=1920, height=1080, fps=FPS, stride=1, frames=rows)


def count(track, arrival: float, departure: float):
    data = data_with(track)
    visit = VehicleVisit(video=VIDEO, visit_id=1, vehicle_track_id=-1,
                          arrival_ts=arrival, departure_ts=departure)
    # Рамка ТС известна на всей записи, а не только внутри окна: машина стоит,
    # рамка каноническая, и именно это даёт счётчику историю трека.
    boxes = {f.frame_idx: np.asarray(BODY, dtype=float) for f in data.frames}
    return count_zone(data, VideoConfig(video=VIDEO), [visit], {1: boxes}, {1: [spec()]})


def test_track_born_before_the_stop_is_judged_by_its_real_birth():
    """Подошёл снаружи до приезда, исчез в двери на стоянке — это вход.

    Без полной истории его рождение читается по первому кадру окна, где он уже
    стоит в проёме, и вход превращается в «нет события».
    """
    events = count(walker(0, 40, 950.0, 700.0), arrival=2.0, departure=8.0)
    assert [e.direction for e in events] == [Direction.IN]


def test_event_outside_the_window_is_not_counted():
    """История берётся целиком, но событие чужого времени — не наше событие."""
    events = count(walker(2, 15, 950.0, 700.0), arrival=2.0, departure=8.0)
    assert events == [], "трек исчез в двери до приезда машины"
