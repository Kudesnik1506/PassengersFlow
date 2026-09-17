"""Наше прежнее решение на стенде: те же два метода, тем же путём.

Оба вызываются через `doorprop.propose_doors`, а не напрямую: постфильтр по
форме и NMS — часть решения, и сравнивать «сырые кандидаты» с чужими готовыми
ответами значило бы сравнивать не то, что работает в бою.

`propose` у обоих методов принимает `TrackData`, но ни один из них его не
трогает — отсюда `None` в вызове. Заводить ради стенда фальшивый кэш треков
было бы хуже: он бы выглядел как настоящий вход и однажды им стал.
"""

from __future__ import annotations

import numpy as np

from ...settings import MODELS_DIR
from ..cases import Case
from ..finders import Box, FrameSource, to_pixels


class _LegacyFinder:
    """Общая обвязка: k кадров вокруг размеченного, полный путь doorprop."""

    tag = ""
    title = ""

    def __init__(self, frames_around: int = 1, step: int = 5) -> None:
        self.frames_around = frames_around
        self.step = step

    def _proposer(self):
        raise NotImplementedError

    def _frame_indices(self, case: Case) -> list[int]:
        """Кадры, с которых собираются кандидаты.

        По умолчанию один — тот самый, на котором нарисован эталон. Больше
        кадров дало бы методу его штатную агрегацию по кворуму, но эталонные
        проёмы размечены на одном кадре, а кузов за секунду успевает качнуться
        на подвеске: кандидат с соседнего кадра сравнивался бы с эталоном,
        снятым в другой момент.
        """
        half = self.frames_around // 2
        return [case.frame_idx + (i - half) * self.step
                for i in range(self.frames_around)]

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        from ...doorprop import propose_doors

        indices = self._frame_indices(case)
        box = np.array(case.body_px, dtype=float)
        left, right = case.truncated
        candidates = propose_doors(
            self._proposer(), frames.path, None, indices,
            {i: box for i in indices},
            truncated_left=left, truncated_right=right,
        )
        return [to_pixels((c.x0, c.y0, c.x1, c.y1), case.body_px) for c in candidates]


class OpenVocabFinder(_LegacyFinder):
    """Метод A: YOLOWorld с запечёнными классами дверей."""

    tag = "openvocab"
    title = "наше: YOLOWorld по словам"

    WEIGHTS = MODELS_DIR / "yolov8s-worldv2-doors.pt"

    def available(self) -> str | None:
        if not self.WEIGHTS.exists():
            return (f"нет запечённых весов {self.WEIGHTS} — "
                    "собрать: python tools/bake_world_doors.py")
        try:
            import ultralytics  # noqa: F401
        except ImportError as exc:
            return f"ultralytics не установлен: {exc}"
        return None

    def _proposer(self):
        from ...doorprop.openvocab import OpenVocabDoorProposer

        return OpenVocabDoorProposer()


class PixelFinder(_LegacyFinder):
    """Метод B: непохожесть на цвет борта в Lab, проём достаёт до низа кузова."""

    tag = "pixels"
    title = "наше: цвет борта (Lab)"

    def available(self) -> str | None:
        try:
            import cv2  # noqa: F401
        except ImportError as exc:
            return f"opencv не установлен: {exc}"
        return None

    def _proposer(self):
        from ...doorprop.pixels import PixelDoorProposer

        return PixelDoorProposer()
