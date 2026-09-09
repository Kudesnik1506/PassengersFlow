"""Кэш треков: тяжёлая детекция отделена от дешёвой логики подсчёта.

Прогон YOLO по видео стоит десятки секунд, а правка логики счёта — секунды.
Поэтому детекция выполняется один раз и складывается в data/cache, а все
бэкенды и эксперименты работают уже с кэшем. Это же даёт честное сравнение:
все варианты считают по одним и тем же детекциям.
"""

from __future__ import annotations

import gzip
import hashlib
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import numpy as np

if TYPE_CHECKING:
    from ..settings import DetectorSettings

from ..settings import CACHE_DIR

CACHE_VERSION = 2


@dataclass
class FrameTracks:
    frame_idx: int
    ts: float
    person_ids: np.ndarray
    person_boxes: np.ndarray
    person_conf: np.ndarray
    vehicle_ids: np.ndarray
    vehicle_boxes: np.ndarray
    vehicle_names: list[str]


@dataclass
class TrackData:
    video: str
    width: int
    height: int
    fps: float
    stride: int
    frames: list[FrameTracks] = field(default_factory=list)

    @property
    def effective_fps(self) -> float:
        return self.fps / self.stride

    @property
    def duration_s(self) -> float:
        return self.frames[-1].ts if self.frames else 0.0


def content_hash(video: Path, chunk: int = 1 << 20) -> str:
    """Короткий хеш содержимого файла.

    Ключом кэша служит содержимое, а не имя и не mtime: mtime непереносим между
    машинами, а имя переживает подмену файла — и тогда старый кэш читается как
    валидный и молча отдаёт треки от другого видео.
    """
    size = video.stat().st_size
    h = hashlib.sha256()
    h.update(str(size).encode())
    with video.open("rb") as fh:
        h.update(fh.read(chunk))
        if size > chunk:
            fh.seek(-chunk, 2)
            h.update(fh.read(chunk))
    return h.hexdigest()[:12]


def cache_path(video: Path, settings: "DetectorSettings | None" = None) -> Path:
    """Путь кэша.

    Версия формата входит в имя файла: при её смене старый кэш просто не
    находится и треки пересчитываются, вместо того чтобы падать при разборе.
    """
    from ..settings import DETECTOR

    st = settings or DETECTOR
    return CACHE_DIR / (
        f"{video.stem}__v{CACHE_VERSION}_{st.tag()}_{content_hash(video)}.json.gz"
    )


def save(data: TrackData, path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "version": CACHE_VERSION,
        "video": data.video,
        "width": data.width,
        "height": data.height,
        "fps": data.fps,
        "stride": data.stride,
        "frames": [
            {
                "i": f.frame_idx,
                "t": round(f.ts, 4),
                "pid": f.person_ids.astype(int).tolist(),
                "pbox": np.round(f.person_boxes, 1).tolist(),
                "pconf": np.round(f.person_conf, 3).tolist(),
                "vid": f.vehicle_ids.astype(int).tolist(),
                "vbox": np.round(f.vehicle_boxes, 1).tolist(),
                "vname": f.vehicle_names,
            }
            for f in data.frames
        ],
    }
    with gzip.open(path, "wt", encoding="utf-8") as fh:
        json.dump(payload, fh)
    return path


def load(path: Path) -> TrackData:
    with gzip.open(path, "rt", encoding="utf-8") as fh:
        payload = json.load(fh)
    if payload.get("version") != CACHE_VERSION:
        raise ValueError(f"несовместимая версия кэша: {path}")
    frames = [
        FrameTracks(
            frame_idx=f["i"],
            ts=f["t"],
            person_ids=np.asarray(f["pid"], dtype=int),
            person_boxes=np.asarray(f["pbox"], dtype=float).reshape(-1, 4),
            person_conf=np.asarray(f["pconf"], dtype=float),
            vehicle_ids=np.asarray(f["vid"], dtype=int),
            vehicle_boxes=np.asarray(f["vbox"], dtype=float).reshape(-1, 4),
            vehicle_names=f["vname"],
        )
        for f in payload["frames"]
    ]
    return TrackData(
        video=payload["video"], width=payload["width"], height=payload["height"],
        fps=payload["fps"], stride=payload["stride"], frames=frames,
    )
