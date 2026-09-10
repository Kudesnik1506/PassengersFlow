"""Фильтр по классу ТС: припаркованная машина не должна становиться визитом.

`VideoConfig.vehicle_classes` существовал в схеме, но нигде не читался —
`vehicle_tracks`/`build_scene` брали все треки ТС подряд. На боевой записи
легковушка (`car`), стоящая у остановки весь день и занимающая ≥5% кадра,
проходила отсев по площади и превращалась в «визит» на часы: у неё нет
активности у дверей (там просто нет дверей в нашем смысле), значит окно счёта
не сужается и остаётся визитом целиком — тысячи кадров в пакет модели.
"""

from __future__ import annotations

import numpy as np
import pytest

from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.core.types import VideoConfig
from paxcount.visits import build_scene, matches_class, vehicle_tracks

FRAME_W, FRAME_H = 1920, 1080


def _box(x0, y0, x1, y1) -> np.ndarray:
    return np.array([x0, y0, x1, y1], dtype=float)


def _stationary_frames(track_id: int, name: str, box, n: int = 60, fps: float = 30.0):
    """Кадры одного неподвижного ТС — тем самым автоматически визит."""
    frames = []
    for i in range(n):
        frames.append(
            FrameTracks(
                frame_idx=i, ts=i / fps,
                person_ids=np.array([], dtype=int),
                person_boxes=np.zeros((0, 4)),
                person_conf=np.array([]),
                vehicle_ids=np.array([track_id]),
                vehicle_boxes=box.reshape(1, 4),
                vehicle_names=[name],
            )
        )
    return frames


@pytest.fixture
def config() -> VideoConfig:
    return VideoConfig(video="synthetic.mp4", doors=[])


def test_matches_class_true_for_allowed_name():
    from paxcount.visits import VehicleTrack

    track = VehicleTrack(track_id=1)
    track.add(0, 0.0, _box(0, 0, 100, 100), "bus")
    track.add(1, 0.1, _box(0, 0, 100, 100), "bus")
    assert matches_class(track, ["bus", "truck", "train"]) is True


def test_matches_class_false_for_disallowed_name():
    from paxcount.visits import VehicleTrack

    track = VehicleTrack(track_id=1)
    track.add(0, 0.0, _box(0, 0, 100, 100), "car")
    track.add(1, 0.1, _box(0, 0, 100, 100), "car")
    assert matches_class(track, ["bus", "truck", "train"]) is False


def test_matches_class_empty_allowlist_matches_everything():
    from paxcount.visits import VehicleTrack

    track = VehicleTrack(track_id=1)
    track.add(0, 0.0, _box(0, 0, 100, 100), "car")
    assert matches_class(track, []) is True


def test_vehicle_tracks_records_names():
    """Имя класса привязано к тому же кадру, что и bbox — не отдельным счётчиком."""
    frames = _stationary_frames(1, "car", _box(100, 100, 700, 500), n=3)
    data = TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=30.0, stride=1, frames=frames)
    tracks = vehicle_tracks(data)
    assert tracks[1].names == ["car", "car", "car"]


def test_parked_car_does_not_become_a_visit(config):
    """Легковушка ≥5% кадра, стоящая всё время — без фильтра дала бы визит."""
    car_box = _box(100.0, 100.0, 700.0, 500.0)  # ~600x400 из 1920x1080 ≈ 11.6%
    frames = _stationary_frames(1, "car", car_box, n=90)  # 3 с при 30 fps
    data = TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=30.0, stride=1, frames=frames)

    scene = build_scene(data, config)
    assert scene.visits == []


def test_bus_still_becomes_a_visit(config):
    """Тот же тест, но с разрешённым классом — фильтр не ломает нормальный путь."""
    bus_box = _box(100.0, 100.0, 700.0, 500.0)
    frames = _stationary_frames(1, "bus", bus_box, n=90)
    data = TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=30.0, stride=1, frames=frames)

    scene = build_scene(data, config)
    assert len(scene.visits) == 1


def test_mixed_car_and_bus_only_bus_counted(config):
    """Два одновременных ТС: легковушка отфильтрована, автобус — нет."""
    car_box = _box(100.0, 100.0, 700.0, 500.0)
    bus_box = _box(900.0, 100.0, 1700.0, 700.0)  # ~800x600 ≈ 23%
    fps = 30.0
    frames = []
    for i in range(90):
        frames.append(
            FrameTracks(
                frame_idx=i, ts=i / fps,
                person_ids=np.array([], dtype=int),
                person_boxes=np.zeros((0, 4)),
                person_conf=np.array([]),
                vehicle_ids=np.array([1, 2]),
                vehicle_boxes=np.vstack([car_box, bus_box]),
                vehicle_names=["car", "bus"],
            )
        )
    data = TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=fps, stride=1, frames=frames)

    scene = build_scene(data, config)
    assert len(scene.visits) == 1
    assert scene.visits[0].vehicle_track_id == 2
