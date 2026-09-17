"""Источник кадров ролика для прогона сравнения.

Кадры читаются по номеру и кэшируются: методы просят один и тот же кадр по
нескольку раз (сам кадр, соседние для разности), а перемотка `CAP_PROP_POS_FRAMES`
по часовому ролику — самая дорогая операция во всём прогоне.
"""

from __future__ import annotations

from pathlib import Path

import numpy as np


class VideoFrames:
    """Кадры одного файла с маленьким кэшем последних обращений."""

    def __init__(self, path: Path, cache_size: int = 24) -> None:
        self.path = path
        self._cache: dict[int, np.ndarray] = {}
        self._order: list[int] = []
        self._cache_size = cache_size

    def frame(self, idx: int) -> np.ndarray:
        if idx in self._cache:
            return self._cache[idx]
        import cv2

        cap = cv2.VideoCapture(str(self.path))
        if not cap.isOpened():
            raise RuntimeError(f"не открывается видео: {self.path}")
        try:
            cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, idx))
            ok, image = cap.read()
        finally:
            cap.release()
        if not ok:
            raise RuntimeError(f"кадр {idx} не читается: {self.path.name}")
        self._cache[idx] = image
        self._order.append(idx)
        while len(self._order) > self._cache_size:
            self._cache.pop(self._order.pop(0), None)
        return image
