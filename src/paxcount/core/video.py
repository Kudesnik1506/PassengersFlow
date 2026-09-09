"""Чтение видео и метаданные. Единая точка входа для всех бэкендов."""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass
from pathlib import Path

import cv2
import numpy as np


@dataclass(frozen=True)
class VideoMeta:
    path: Path
    width: int
    height: int
    fps: float
    frame_count: int

    @property
    def duration_s(self) -> float:
        return self.frame_count / self.fps if self.fps else 0.0

    def describe(self) -> str:
        return (
            f"{self.path.name}: {self.width}x{self.height}, "
            f"{self.fps:.2f} fps, {self.frame_count} кадров, {self.duration_s:.1f} с"
        )


def probe(path: Path) -> VideoMeta:
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"не открывается видео: {path}")
    try:
        return VideoMeta(
            path=path,
            width=int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)),
            height=int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)),
            fps=float(cap.get(cv2.CAP_PROP_FPS)) or 30.0,
            frame_count=int(cap.get(cv2.CAP_PROP_FRAME_COUNT)),
        )
    finally:
        cap.release()


def frames(path: Path, stride: int = 1) -> Iterator[tuple[int, float, np.ndarray]]:
    """Отдаёт (индекс кадра, timestamp в секундах, кадр BGR).

    ``stride`` прореживает кадры: 2 — каждый второй. Timestamp считается по
    индексу и fps, а не по CAP_PROP_POS_MSEC, который на ogv/webm врёт.
    """
    meta = probe(path)
    cap = cv2.VideoCapture(str(path))
    if not cap.isOpened():
        raise RuntimeError(f"не открывается видео: {path}")
    idx = 0
    try:
        while True:
            ok, frame = cap.read()
            if not ok:
                break
            if idx % stride == 0:
                yield idx, idx / meta.fps, frame
            idx += 1
    finally:
        cap.release()
