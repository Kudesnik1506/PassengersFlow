"""Окна счёта для модели: сужение визита до активности плюс бюджет.

`build_windows` не изобретает новую логику сужения — она зовёт
`zonecount.activity_window` (принцип 5). Тест проверяет три вещи, которых
раньше не было: пустая ручная разметка не должна означать «весь визит»
(фолбэк должен сработать и здесь тоже), два одновременных ТС должны получить
признак `overlap`, а оценка токенов должна расти с длиной окна и падать при
масштабировании кропа.
"""

from __future__ import annotations

import numpy as np
import pytest

from conftest import BUS, box, walk, zone_spec
from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.core.types import VideoConfig
from paxcount.visits import build_scene
from paxcount.windows import Window, build_windows

FRAME_W, FRAME_H = 1920, 1080
FPS = 30.0


def _bus_frame(idx: int, ts: float, person_boxes: list[np.ndarray], vehicle_box: np.ndarray):
    return FrameTracks(
        frame_idx=idx, ts=ts,
        person_ids=np.arange(len(person_boxes)),
        person_boxes=(
            np.asarray(person_boxes, dtype=float) if person_boxes else np.zeros((0, 4))
        ),
        person_conf=np.ones(len(person_boxes)),
        vehicle_ids=np.array([1]),
        vehicle_boxes=vehicle_box.reshape(1, 4),
        vehicle_names=["bus"],
    )


def _data_with_person_near_door(door_zone: tuple[float, float, float, float]) -> TrackData:
    """Визит 0..10 с, ТС стоит, человек стоит у зоны двери 4..6 с."""
    bus = box(*BUS)
    cx = BUS[0] + (door_zone[0] + door_zone[2]) / 2 * (BUS[2] - BUS[0])
    cy = BUS[1] + (door_zone[1] + door_zone[3]) / 2 * (BUS[3] - BUS[1])
    walk_boxes = walk((cx, cy), (cx, cy), steps=60)  # 2 с неподвижно у двери
    walk_start = 120  # 4.0 с
    frames = []
    for i in range(300):  # 10 с при 30 fps
        pboxes = []
        if walk_start <= i < walk_start + len(walk_boxes):
            pboxes = [walk_boxes[i - walk_start]]
        frames.append(_bus_frame(i, i / FPS, pboxes, bus))
    return TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=FPS, stride=1, frames=frames)


def _two_vehicles(same_time: bool) -> TrackData:
    bus_a = box(200.0, 300.0, 700.0, 700.0)
    bus_b = box(1200.0, 300.0, 1700.0, 700.0)
    frames = []
    for i in range(300):
        ts = i / FPS
        if same_time:
            vids, vboxes, vnames = [1, 2], [bus_a, bus_b], ["bus", "bus"]
        elif i < 150:
            vids, vboxes, vnames = [1], [bus_a], ["bus"]
        else:
            vids, vboxes, vnames = [2], [bus_b], ["bus"]
        frames.append(
            FrameTracks(
                frame_idx=i, ts=ts,
                person_ids=np.array([], dtype=int),
                person_boxes=np.zeros((0, 4)),
                person_conf=np.array([]),
                vehicle_ids=np.array(vids),
                vehicle_boxes=np.asarray(vboxes, dtype=float).reshape(len(vids), 4),
                vehicle_names=vnames,
            )
        )
    return TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=FPS, stride=1, frames=frames)


def test_window_narrows_to_activity_near_door():
    cfg = VideoConfig(video="s.mp4", doors=[zone_spec((0.0, 0.65, 1.0, 1.12))])
    data = _data_with_person_near_door(cfg.doors[0].zone)
    scene = build_scene(data, cfg)
    assert len(scene.visits) == 1

    windows = build_windows(data, cfg, scene)
    assert len(windows) == 1
    w = windows[0]
    # активность 4..6 с плюс guard 1 с с каждой стороны = 3..7 с
    assert w.t0 == pytest.approx(3.0, abs=0.2)
    assert w.t1 == pytest.approx(7.0, abs=0.2)
    assert w.duration < 9.9  # уже, чем весь 10-секундный визит


def test_empty_manual_doors_still_falls_back():
    """cfg.doors=[] (ручная разметка без дверей) не должно читаться как «без окна»."""
    cfg = VideoConfig(video="s.mp4", doors=[], manual=True)
    data = _data_with_person_near_door((0.0, 0.65, 1.0, 1.12))
    scene = build_scene(data, cfg)
    windows = build_windows(data, cfg, scene)
    assert windows[0].duration < 9.9


def test_person_px_is_median_height_in_window():
    cfg = VideoConfig(video="s.mp4", doors=[zone_spec((0.0, 0.65, 1.0, 1.12))])
    data = _data_with_person_near_door(cfg.doors[0].zone)
    scene = build_scene(data, cfg)
    w = build_windows(data, cfg, scene)[0]
    assert w.person_px == pytest.approx(120.0, rel=0.05)  # person_h по умолчанию в walk()


def test_overlap_true_for_simultaneous_visits():
    cfg = VideoConfig(video="s.mp4", doors=[])
    data = _two_vehicles(same_time=True)
    scene = build_scene(data, cfg)
    assert len(scene.visits) == 2
    assert all(w.overlap for w in build_windows(data, cfg, scene))


def test_overlap_false_for_sequential_visits():
    cfg = VideoConfig(video="s.mp4", doors=[])
    data = _two_vehicles(same_time=False)
    scene = build_scene(data, cfg)
    assert len(scene.visits) == 2
    assert not any(w.overlap for w in build_windows(data, cfg, scene))


def test_tokens_estimate_scales_with_window_length():
    short = Window(visit_id=1, t0=0.0, t1=5.0, box=(0.0, 0.0, 500.0, 400.0),
                    person_px=120.0, overlap=False)
    long = Window(visit_id=1, t0=0.0, t1=10.0, box=(0.0, 0.0, 500.0, 400.0),
                   person_px=120.0, overlap=False)
    assert long.tokens_estimate() == pytest.approx(short.tokens_estimate() * 2, rel=0.05)


def test_tokens_estimate_scales_crop_to_max_width():
    wide = Window(visit_id=1, t0=0.0, t1=5.0, box=(0.0, 0.0, 1920.0, 400.0),
                   person_px=None, overlap=False)
    narrow = Window(visit_id=1, t0=0.0, t1=5.0, box=(0.0, 0.0, 960.0, 200.0),
                     person_px=None, overlap=False)
    assert wide.tokens_estimate() == narrow.tokens_estimate()


def test_tokens_estimate_zero_without_box():
    w = Window(visit_id=1, t0=0.0, t1=5.0, box=None, person_px=None, overlap=False)
    assert w.tokens_estimate() == 0
