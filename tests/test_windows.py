"""Окна счёта: сужение до активности, бюджет и соседство машин.

`build_windows` не изобретает сужение заново — зовёт `zonecount.activity_window`
(принцип 5). Здесь проверяется то, чего в ней нет.

Главное изменение против первой версии — **одновременность и помеха это разные
вещи**. На боевой остановке 46 % машин делят минуту прибытия с соседней (замер
по принятой таблице заказчика). Если каждую такую пару слать человеку, очередь
проверки составит 144 строки из 312 и никакой час её не вместит. Но само по
себе соседство безвредно: две машины у разных краёв остановки считаются
независимо, каждая по своему кропу. Опасно другое — когда чужой корпус
накрывает дверную зону нашей машины: тогда человек у чужой двери может попасть
в наш счёт. Вот это и уходит в проверку.
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
    walk_boxes = walk((cx, cy), (cx, cy), steps=60)
    walk_start = 120
    frames = []
    for i in range(300):
        pboxes = []
        if walk_start <= i < walk_start + len(walk_boxes):
            pboxes = [walk_boxes[i - walk_start]]
        frames.append(_bus_frame(i, i / FPS, pboxes, bus))
    return TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=FPS, stride=1, frames=frames)


def _two_vehicles(same_time: bool, apart: bool = True) -> TrackData:
    """Две машины: врозь по кадру или вплотную, одновременно или по очереди."""
    bus_a = box(200.0, 300.0, 700.0, 700.0)
    bus_b = box(1200.0, 300.0, 1700.0, 700.0) if apart else box(690.0, 300.0, 1190.0, 700.0)
    frames = []
    for i in range(300):
        if same_time:
            vids, vboxes, vnames = [1, 2], [bus_a, bus_b], ["bus", "bus"]
        elif i < 150:
            vids, vboxes, vnames = [1], [bus_a], ["bus"]
        else:
            vids, vboxes, vnames = [2], [bus_b], ["bus"]
        frames.append(
            FrameTracks(
                frame_idx=i, ts=i / FPS,
                person_ids=np.array([], dtype=int),
                person_boxes=np.zeros((0, 4)),
                person_conf=np.array([]),
                vehicle_ids=np.array(vids),
                vehicle_boxes=np.asarray(vboxes, dtype=float).reshape(len(vids), 4),
                vehicle_names=vnames,
            )
        )
    return TrackData(video="s", width=FRAME_W, height=FRAME_H, fps=FPS, stride=1, frames=frames)


def _windows(data: TrackData, cfg: VideoConfig) -> list[Window]:
    return build_windows(data, cfg, build_scene(data, cfg))


# ---- Сужение окна ----------------------------------------------------------


def test_window_narrows_to_activity_near_door():
    cfg = VideoConfig(video="s.mp4", doors=[zone_spec((0.0, 0.65, 1.0, 1.12))])
    data = _data_with_person_near_door(cfg.doors[0].zone)
    windows = _windows(data, cfg)
    assert len(windows) == 1
    w = windows[0]
    assert w.t0 == pytest.approx(3.0, abs=0.2)
    assert w.t1 == pytest.approx(7.0, abs=0.2)
    assert w.duration < 9.9


def test_empty_manual_doors_still_falls_back():
    cfg = VideoConfig(video="s.mp4", doors=[], manual=True)
    data = _data_with_person_near_door((0.0, 0.65, 1.0, 1.12))
    assert _windows(data, cfg)[0].duration < 9.9


def test_person_px_is_median_height_in_window():
    cfg = VideoConfig(video="s.mp4", doors=[zone_spec((0.0, 0.65, 1.0, 1.12))])
    data = _data_with_person_near_door(cfg.doors[0].zone)
    assert _windows(data, cfg)[0].person_px == pytest.approx(120.0, rel=0.05)


# ---- Соседство: одновременность отдельно, помеха отдельно ------------------


def test_simultaneous_but_separated_vehicles_are_not_contested():
    """Две машины у разных краёв остановки считаются независимо.

    Это и есть те 46 %, которые нельзя слать человеку: соседство по времени
    само по себе счёту не мешает, если корпуса не накрывают чужие двери.
    """
    cfg = VideoConfig(video="s.mp4", doors=[])
    data = _two_vehicles(same_time=True, apart=True)
    windows = _windows(data, cfg)
    assert len(windows) == 2
    assert all(w.rivals for w in windows), "по времени соседство есть"
    assert not any(w.contested for w in windows), "но дверным зонам оно не мешает"


def test_adjacent_vehicles_contest_each_others_doors():
    """Машина вплотную накрывает дверную зону соседа — это в проверку."""
    cfg = VideoConfig(video="s.mp4", doors=[])
    data = _two_vehicles(same_time=True, apart=False)
    windows = _windows(data, cfg)
    assert len(windows) == 2
    assert any(w.contested for w in windows)


def test_sequential_vehicles_have_no_rivals():
    cfg = VideoConfig(video="s.mp4", doors=[])
    data = _two_vehicles(same_time=False)
    windows = _windows(data, cfg)
    assert not any(w.rivals for w in windows)
    assert not any(w.contested for w in windows)


def test_rivals_name_the_other_visit():
    """Соперник назван по номеру визита — промпту надо сказать, кого не считать."""
    cfg = VideoConfig(video="s.mp4", doors=[])
    windows = _windows(_two_vehicles(same_time=True), cfg)
    ids = {w.visit_id for w in windows}
    for w in windows:
        assert set(w.rivals) == ids - {w.visit_id}


# ---- Бюджет ----------------------------------------------------------------


def _window(**over) -> Window:
    base = dict(visit_id=1, t0=0.0, t1=5.0, box=(0.0, 0.0, 500.0, 400.0),
                person_px=120.0, rivals=(), contested=False)
    base.update(over)
    return Window(**base)


def test_tokens_estimate_scales_with_window_length():
    assert _window(t1=10.0).tokens_estimate() == pytest.approx(
        _window(t1=5.0).tokens_estimate() * 2, rel=0.05)


def test_tokens_estimate_scales_crop_to_max_width():
    wide = _window(box=(0.0, 0.0, 1920.0, 400.0), person_px=None)
    narrow = _window(box=(0.0, 0.0, 960.0, 200.0), person_px=None)
    assert wide.tokens_estimate() == narrow.tokens_estimate()


def test_tokens_estimate_zero_without_box():
    assert _window(box=None, person_px=None).tokens_estimate() == 0


# ---- Очередь проверки ------------------------------------------------------


def test_clean_window_needs_no_review():
    assert _window().review_reasons() == []


def test_contested_window_goes_to_review():
    assert "чужой корпус" in " ".join(_window(contested=True).review_reasons())


def test_tiny_people_go_to_review():
    """Ниже MIN_PERSON_PX человека не различить — это задача камеры."""
    from paxcount.settings import MIN_PERSON_PX

    reasons = _window(person_px=MIN_PERSON_PX - 1).review_reasons()
    assert any("мелко" in r for r in reasons)


def test_window_without_people_says_so():
    assert "людей в окне не найдено" in _window(person_px=None).review_reasons()


def test_rivals_alone_do_not_trigger_review():
    """Соседство по времени — не повод: иначе очередь съест половину смены."""
    assert _window(rivals=(2, 3)).review_reasons() == []
