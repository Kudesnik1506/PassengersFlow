"""Границы признака выхода: что им засчитывается, а что нет.

Выход — появление в проёме и шаг НАРУЖУ, поперёк корпуса. Сторона не важна:
уйти можно и к камере, и от неё (решение 065). А вот ослабление до «удаления от
проёма в любую сторону», снимающее саму поперечность, отменено: без неё прохожий
вдоль борта и вышедший вдоль борта неразличимы (решение 055).

Случаи, которые набор держит: шаг к камере — выход; шаг от камеры поперёк борта
— тоже выход; уход ВВЕРХ, в салон, — вход, а не выход и не «нет события»
(решение 053); топтание у двери — не событие вовсе; проход ВДОЛЬ борта — не
событие.
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


# ---- Выход вверх по кадру, но поперёк корпуса --------------------------------
#
# Решение 055 отменяло ослабление до «удаления от проёма в ЛЮБУЮ сторону»: оно
# снимало и требование поперечности, и вышедший становился неотличим от
# прохожего вдоль борта. Здесь снимается только знак: человек по-прежнему обязан
# идти ПОПЕРЁК корпуса, а не вдоль, но уходить он может и от камеры.
#
# Основание — замер на боевых визитах после починки зоны (060) и порога (063):
# выходы 3 → 4 из 6, входы те же 18, ложных выходов не появилось ни на одном
# визите с эталоном «0», отладочный набор не сдвинулся ни на единицу.
# Разрешено владельцем.

def test_exit_away_from_the_camera_is_counted():
    """Вышел и пошёл ОТ камеры, поперёк борта, мимо салона — это выход.

    Кузов кончается на 900 px по горизонтали; человек уходит на 1100, то есть
    наружу, а не в салон. Требование поперечности соблюдено: по вертикали он
    проходит вчетверо больше, чем по горизонтали.
    """
    walk = [(1000.0 + 20.0 * i, 700.0 - 60.0 * i) for i in range(6)]
    events = count([(550.0, 700.0), (700.0, 700.0)] + walk)
    assert [e.direction for e in events] == [Direction.OUT]
