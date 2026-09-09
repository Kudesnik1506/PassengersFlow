"""Построение кэша треков: единственное место, где запускается детектор."""

from __future__ import annotations

import time
from pathlib import Path

from .core.detect import Detector
from .core.trackdata import FrameTracks, TrackData, cache_path, load, save
from .core.video import frames, probe
from .runlog import setup_logger
from .settings import DETECTOR, DetectorSettings
from .stitching import stitch_person_tracks

log = setup_logger("tracking")


def build_tracks(
    video: Path,
    detector: Detector,
    stride: int = 1,
    progress=None,
) -> TrackData:
    meta = probe(video)
    detector.reset()
    data = TrackData(
        video=video.name, width=meta.width, height=meta.height,
        fps=meta.fps, stride=stride,
    )
    for frame_idx, ts, frame in frames(video, stride=stride):
        t = detector.track(frame)
        data.frames.append(
            FrameTracks(
                frame_idx=frame_idx, ts=ts,
                person_ids=t.person_ids, person_boxes=t.person_boxes,
                person_conf=t.person_conf, vehicle_ids=t.vehicle_ids,
                vehicle_boxes=t.vehicle_boxes, vehicle_names=t.vehicle_names,
            )
        )
        if progress is not None:
            progress(len(data.frames), meta.frame_count // max(stride, 1))
    return data


def get_tracks(
    video: Path,
    settings: DetectorSettings | None = None,
    device: str | None = None,
    refresh: bool = False,
    progress=None,
) -> tuple[TrackData, bool, float]:
    """Возвращает (треки, взято_из_кэша, секунды)."""
    st = settings or DETECTOR
    path = cache_path(video, st)
    if path.exists() and not refresh:
        t0 = time.perf_counter()
        data = load(path)
        data = stitch_person_tracks(data)
        elapsed = time.perf_counter() - t0
        log.info(
            "кэш треков: %s (%d кадров, %.2f с)", path.name, len(data.frames), elapsed
        )
        return data, True, elapsed

    detector = Detector(
        weights=st.weights, device=device, conf=st.conf,
        imgsz=st.imgsz, tracker=st.tracker,
    )
    log.info(
        "детекция: %s (модель %s, imgsz %d, трекер %s, устройство %s)",
        video.name, st.weights, st.imgsz, st.tracker, detector.device,
    )
    t0 = time.perf_counter()
    data = build_tracks(video, detector, stride=st.stride, progress=progress)
    elapsed = time.perf_counter() - t0
    save(data, path)
    data = stitch_person_tracks(data)
    log.info(
        "детекция готова: %d кадров за %.1f с (%.1f кадр/с) → %s",
        len(data.frames), elapsed, len(data.frames) / max(elapsed, 1e-9), path.name,
    )
    return data, False, elapsed
