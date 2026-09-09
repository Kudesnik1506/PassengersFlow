"""Единая точка правды для тестовых данных (принципы 2 и 5).

Сцены собираются здесь и переиспользуются всеми тестами. Заводить bbox или трек
прямо в тесте нельзя: назавтра копии разойдутся, и половина набора начнёт
проверять поведение, которого в проекте уже нет.

Данные синтетические — числа, а не кадры. Персональные данные в тесты не
попадают (принцип 6), видео и веса не нужны (принцип 7).
"""

from __future__ import annotations

import numpy as np
import pytest

from paxcount.core.types import DoorSpec, Point, VehicleVisit
from paxcount.visits import VehicleTrack
from paxcount.zonecount import TrackLife

# Размер кадра во всех сценах. Реальный набор — 1920×1080, берём его же:
# граница кадра участвует в логике (`_touches_border`), и играть с ней на
# выдуманных числах значит проверять не тот масштаб.
FRAME_W, FRAME_H = 1920, 1080

# Опорный автобус: стоит в середине кадра, до краёв далеко. Числа близки к
# реальному визиту на видео 09 (канонический bbox ~350..900 по x).
BUS = (350.0, 300.0, 900.0, 700.0)


def box(x0: float, y0: float, x1: float, y1: float) -> np.ndarray:
    return np.array([x0, y0, x1, y1], dtype=float)


@pytest.fixture
def bus_box() -> np.ndarray:
    return box(*BUS)


@pytest.fixture
def frame_size() -> tuple[int, int]:
    return FRAME_W, FRAME_H


def make_visit(
    arrival_ts: float = 0.0,
    departure_ts: float = 30.0,
    visit_id: int = 1,
    vehicle_track_id: int = 1,
    **kwargs,
) -> VehicleVisit:
    """Визит по умолчанию длинный: 30 с.

    Длина важна: `edge_guard_seconds` берёт минимум из 0.6 с и 15 % визита,
    поэтому на коротком визите защитная зона у краёв другая. Тесты, которым
    нужен именно короткий визит, задают его явно.
    """
    return VehicleVisit(
        video="synthetic",
        visit_id=visit_id,
        vehicle_track_id=vehicle_track_id,
        arrival_ts=arrival_ts,
        departure_ts=departure_ts,
        **kwargs,
    )


@pytest.fixture
def visit() -> VehicleVisit:
    return make_visit()


def zone_spec(
    zone: tuple[float, float, float, float],
    door_id: str = "door1",
    frame: str = "vehicle",
) -> DoorSpec:
    """Дверь в режиме зоны.

    ``line_start``/``line_end`` у модели обязательны, но в режиме ``zone`` не
    участвуют в решении — задаём заглушку здесь, в одном месте, а не в каждом
    тесте.
    """
    return DoorSpec(
        door_id=door_id,
        mode="zone",
        frame=frame,
        zone=zone,
        line_start=Point(x=0.0, y=0.0),
        line_end=Point(x=1.0, y=0.0),
    )


def make_vehicle_track(
    track_id: int,
    boxes: dict[int, np.ndarray],
    fps: float = 30.0,
) -> VehicleTrack:
    track = VehicleTrack(track_id=track_id)
    for frame_idx in sorted(boxes):
        track.add(frame_idx, frame_idx / fps, boxes[frame_idx])
    return track


def walk(
    start: tuple[float, float],
    end: tuple[float, float],
    steps: int = 20,
    person_w: float = 40.0,
    person_h: float = 120.0,
) -> list[np.ndarray]:
    """Череда рамок человека, идущего из точки в точку.

    Точки задаются как точка опоры — низ-центр рамки: именно её считает
    `_anchor`, и именно по ней принимается решение о событии. Задавать здесь
    углы рамки значило бы проверять не ту величину.

    Шагов по умолчанию 20: при 30 fps это 0.63 с, то есть длиннее порога
    `MIN_TRACK_SECONDS` (0.5 с). Короче — и любой тест упрётся в отсев по длине
    трека вместо той логики, которую он проверяет.
    """
    boxes = []
    for i in range(steps):
        t = i / max(steps - 1, 1)
        cx = start[0] + (end[0] - start[0]) * t
        cy = start[1] + (end[1] - start[1]) * t
        boxes.append(box(cx - person_w / 2, cy - person_h, cx + person_w / 2, cy))
    return boxes


def make_life(
    person_boxes: list[np.ndarray],
    first_in_zone: bool,
    last_in_zone: bool,
    first_ts: float = 5.0,
    fps: float = 30.0,
    track_id: int = 7,
) -> TrackLife:
    """Собирает TrackLife так же, как это делает `collect_lives`.

    Поля заполняются по тем же правилам, что в рабочем коде: точки опоры — из
    рамок, время — из числа кадров. Расхождение здесь означало бы, что тесты
    проверяют структуру, которой в пайплайне не бывает.
    """
    anchors = [(float((b[0] + b[2]) / 2), float(b[3])) for b in person_boxes]
    last_ts = first_ts + (len(person_boxes) - 1) / fps
    return TrackLife(
        track_id=track_id,
        first_ts=first_ts,
        last_ts=last_ts,
        first_frame=int(first_ts * fps),
        last_frame=int(last_ts * fps),
        first_box=person_boxes[0],
        last_box=person_boxes[-1],
        first_in_zone=first_in_zone,
        last_in_zone=last_in_zone,
        frames=len(person_boxes),
        in_zone_frames=len(person_boxes),
        anchors=anchors,
    )
