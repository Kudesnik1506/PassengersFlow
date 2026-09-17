"""Детекция людей на окнах стоянок, а не на всей записи.

Боевой ролик идёт двадцать минут, стоянка — пятнадцать секунд. Гонять детектор
по всему файлу ради шести стоянок значит платить часами за минуты (решение
022), поэтому кадры вне окон только пролистываются: `grab` без `retrieve` не
раскодирует кадр вовсе, и проход по файлу стоит секунды.

Кэш — на окно, а не на файл, и в имени у него и настройки детектора, и
границы окна: кэш от другого окна не должен молча подойти по имени. Ключ
содержимого файла берётся общий с основным кэшем (`content_hash`), чтобы
подмена ролика обесценила и его тоже.

Трекер между окнами сбрасывается. Иначе человек, стоявший у двери в конце
одного окна, продолжил бы тот же идентификатор в следующем, и его вход был бы
приписан машине, которой он ждал.
"""

from __future__ import annotations

from pathlib import Path

import cv2

from ..core.trackdata import FrameTracks, TrackData, content_hash, load, save
from ..core.video import probe
from ..settings import CACHE_DIR, DetectorSettings, detector_for
from ..stitching import stitch_person_tracks

Window = tuple[int, int]


def window_cache_path(video: Path, settings: DetectorSettings, window: Window) -> Path:
    start, end = window
    return CACHE_DIR / (
        f"{video.stem}__win_{settings.tag()}_{content_hash(video)}_"
        f"f{start}-{end}.json.gz"
    )


def tracks_in_windows(
    video: Path,
    windows: dict[str, Window],
    settings: DetectorSettings | None = None,
    device: str | None = None,
    refresh: bool = False,
    progress=None,
) -> dict[str, TrackData]:
    """Треки по каждому окну. Готовое берётся из кэша, недостающее считается."""
    st = settings or detector_for(video)
    paths = {key: window_cache_path(video, st, w) for key, w in windows.items()}

    out: dict[str, TrackData] = {}
    pending: dict[str, Window] = {}
    for key, window in windows.items():
        if paths[key].exists() and not refresh:
            out[key] = stitch_person_tracks(load(paths[key]))
        else:
            pending[key] = window
    if not pending:
        return out

    from ..core.detect import Detector, tracker_yaml

    meta = probe(video)
    # Конфиг трекера берётся у настроек, а не напрямую `st.tracker`: память
    # трекера задана в кадрах и обязана расти вместе с частотой, иначе переход
    # на покадровость втрое её укоротит (settings.DetectorSettings).
    detector = Detector(weights=st.weights, device=device, conf=st.conf,
                         imgsz=st.imgsz, tracker=tracker_yaml(st))
    built = {
        key: TrackData(video=video.name, width=meta.width, height=meta.height,
                        fps=meta.fps, stride=st.stride)
        for key in pending
    }
    last_end = max(end for _, end in pending.values())

    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"не открывается видео: {video}")
    current: str | None = None
    try:
        for idx in range(last_end + 1):
            if not cap.grab():
                break
            key = next((k for k, (s, e) in pending.items() if s <= idx <= e), None)
            if key is None or idx % st.stride:
                continue
            if key != current:          # новое окно — трекер начинается заново
                detector.reset()
                current = key
            ok, frame = cap.retrieve()
            if not ok:
                break
            t = detector.track(frame)
            built[key].frames.append(FrameTracks(
                frame_idx=idx, ts=idx / meta.fps,
                person_ids=t.person_ids, person_boxes=t.person_boxes,
                person_conf=t.person_conf, vehicle_ids=t.vehicle_ids,
                vehicle_boxes=t.vehicle_boxes, vehicle_names=t.vehicle_names,
            ))
            if progress is not None:
                progress(key, idx, last_end)
    finally:
        cap.release()

    for key, data in built.items():
        save(data, paths[key])
        out[key] = stitch_person_tracks(data)
    return out
