"""Вход — это уход из дверной зоны внутрь кузова, а не обязательно исчезновение.

Зонный счёт был построен на допущении: вошедший пропадает за корпусом. На К2
автобус занимает кадр целиком, и допущение ложно — пассажирку видно сквозь
стекло уже в салоне. Замер на визите 1: трек живёт с 50.6 до 59.3 с, проходит
дверную зону и умирает на 656 px, то есть выше полосы ног (680 px) и внутри
рамки кузова. Событие не засчитывалось, хотя вход был.

Новый признак закрывает оба случая сразу: и когда человека закрывает борт, и
когда его видно внутри. Но он же и опаснее — «выше полосы ног» на К3, где
автобус не во весь кадр, это ещё и тротуар за машиной. Поэтому проверок две:
попасть в салон (внутрь рамки кузова по горизонтали) и прийти туда снизу.
"""

from __future__ import annotations

import numpy as np
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


def path(points: list[tuple[float, float]]) -> dict[int, tuple]:
    """Точка опоры по кадрам → рамки человека ростом 100 px."""
    return {i: (x - 25.0, y - 100.0, x + 25.0, y) for i, (x, y) in enumerate(points)}


def count(track: dict[int, tuple], arrival=0.5, departure=8.0):
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
                          arrival_ts=arrival, departure_ts=departure)
    boxes = {f.frame_idx: np.asarray(BODY, dtype=float) for f in data.frames}
    return count_zone(data, VideoConfig(video=VIDEO), [visit], {1: boxes}, {1: [spec()]})


def rising(x_end: float) -> dict[int, tuple]:
    """Подошёл снизу, прошёл дверную зону, поднялся выше неё и остался стоять."""
    walk = [(550.0, 950.0 - 25.0 * i) for i in range(14)]        # 950 → 625
    inside = [(x_end, 600.0)] * 12                                # стоит выше зоны
    return path(walk + inside)


def test_passenger_seen_inside_the_cabin_is_counted_as_boarding():
    events = count(rising(x_end=550.0))
    assert [e.direction for e in events] == [Direction.IN]


def test_person_who_ended_up_behind_the_vehicle_is_not_boarding():
    """Выше полосы ног — ещё не салон: за бортом там тротуар, а не двери."""
    assert count(rising(x_end=1000.0)) == [], "кузов кончается на 900 px"


def test_boarding_is_dated_by_the_moment_at_the_door_not_by_the_death_of_the_track():
    """Вошедшая стоит в салоне ещё полминуты — трек умирает далеко за окном.

    Замер на визите 1: окно счёта 50.1-55.6 с, пассажирка последний раз у двери
    на 55.5 с, а трек живёт до 59.3 — детектор видит её сквозь стекло. Датировать
    вход смертью трека значит вынести событие за окно визита и потерять его.
    """
    walk = [(550.0, 950.0 - 25.0 * i) for i in range(14)]   # 950 → 625, до 1.3 с
    standing = [(550.0, 600.0)] * 56                         # стоит в салоне до 6.9 с
    events = count(path(walk + standing), arrival=0.5, departure=3.0)
    assert [e.direction for e in events] == [Direction.IN]
    assert 0.5 <= events[0].event_ts <= 3.0, "событие датировано моментом у двери"


def test_track_that_outlives_the_window_is_not_treated_as_cut_off_by_it():
    """«Оборвался на конце визита» — про обрыв у границы, а не про жизнь за ней.

    После того как история трека перестала обрезаться окном, трек, живущий
    дольше визита, давал отрицательный запас до конца — и читался как
    оборвавшийся ровно на границе.
    """
    walk = [(550.0, 950.0 - 25.0 * i) for i in range(14)]
    standing = [(550.0, 600.0)] * 56
    assert count(path(walk + standing), arrival=0.5, departure=3.0), "событие есть"


# ---- Салон считается по СВОЕЙ двери ------------------------------------------
#
# «Выше полосы ног» — признак салона, и порог у него брался один на весь кузов:
# `min` по всем дверям. Пока полоса ног общая, это одно и то же число, и разницы
# нет. Но замер показал, что общая полоса не достаёт до дверей с высоким порогом
# (решение 062), а как только полосу разводят по дверям, `min` берёт САМУЮ
# ВЫСОКУЮ границу и объявляет салоном лишь узкую щель под крышей: вошедший в
# низкую дверь перестаёт считаться.
#
# Признак обязан спрашивать ту дверь, у которой человек стоит.

TWO_DOORS = (
    (500.0, 625.0, 600.0, 860.0),   # д1: полоса ниже
    (200.0, 450.0, 300.0, 700.0),   # д2: полоса заметно выше
)


def two_door_specs() -> list[DoorSpec]:
    return [
        DoorSpec(door_id=f"д{i + 1}", mode="zone", frame="absolute",
                  line_start={"x": z[0], "y": z[1]},
                  line_end={"x": z[2], "y": z[3]}, zone=z)
        for i, z in enumerate(TWO_DOORS)
    ]


def count_two(track: dict[int, tuple]):
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
    return count_zone(data, VideoConfig(video=VIDEO), [visit], {1: boxes},
                       {1: two_door_specs()})


def test_cabin_is_judged_by_the_door_the_person_stands_at():
    """Вошёл в НИЗКУЮ дверь — судим по её полосе, а не по полосе соседней.

    Человек проходит зону д1 (полоса 625..860) и останавливается на 600 px:
    выше своей полосы и внутри кузова, то есть в салоне. Полоса д2 начинается с
    450, и общий порог по `min` объявил бы салоном только выше 450 — вход
    потерялся бы из-за двери, у которой человек не стоял.
    """
    assert [e.direction for e in count_two(rising(x_end=550.0))] == [Direction.IN]


def test_cabin_test_still_refuses_the_pavement_behind_the_vehicle():
    """Свой порог не значит «любой»: за рамкой кузова салона по-прежнему нет."""
    assert count_two(rising(x_end=1000.0)) == [], "кузов кончается на 900 px"
