"""Метод A: open-vocabulary детекция дверей моделью YOLOWorld.

Работает по текстовому промпту, без дообучения — на боевых видео сравнивается
с методом B (пиксельным, ``pixels.py``), у которых разная природа ошибок:
метод B слеп к контрасту, если дверь плохо отличается от борта по цвету;
метод A слеп, если модель просто не знает понятия «дверь автобуса» и путает
её с окном. Один не подменяет другой, поэтому оба остаются в проекте.

Рантайм грузит **только запечённые веса** (``models/yolov8s-worldv2-doors.pt``,
готовит их ``tools/bake_world_doors.py``) и поэтому не тянет CLIP. Если файла
нет — это явная ошибка конфигурации, а не повод втихую поставить CLIP и
скачать что-то из сети во время рабочего прогона.
"""

from __future__ import annotations

import logging
from pathlib import Path

import numpy as np

from ..core.trackdata import TrackData
from ..settings import MODELS_DIR
from . import DoorCandidate

log = logging.getLogger("paxcount.doorprop.openvocab")

SOURCE_WEIGHTS = str(MODELS_DIR / "yolov8s-worldv2.pt")
BAKED_WEIGHTS = MODELS_DIR / "yolov8s-worldv2-doors.pt"

# Ярус 1 — целимся прямо в дверь. Ярус 2 — общее слово, пробуем, если ярус 1
# ничего не нашёл ни на одном опорном кадре: возможно, конкретная формулировка
# модели незнакома, а более общая — знакома.
TIER1_PROMPTS = [
    "bus door", "open bus door", "doorway", "entrance of a bus", "stairs into a bus",
]
TIER2_PROMPTS = ["door", "dark opening in the side of a bus"]
# Отвлекающие классы детектируются и сразу выбрасываются — без них модель
# путает контрастное окно с дверью ровно так, как опасался заказчик.
DISTRACTOR_PROMPTS = [
    "bus window", "windshield", "wheel", "person", "bus", "advertisement panel",
]
ALL_PROMPTS = TIER1_PROMPTS + TIER2_PROMPTS + DISTRACTOR_PROMPTS
_TIER1_IDX = set(range(0, len(TIER1_PROMPTS)))
_TIER2_IDX = set(range(len(TIER1_PROMPTS), len(TIER1_PROMPTS) + len(TIER2_PROMPTS)))

CONF = 0.03  # низкий намеренно: zero-shot на редком понятии даёт малую уверенность


class OpenVocabDoorProposer:
    method_tag = "openvocab"

    def __init__(self) -> None:
        self._model = None

    def _load(self):
        if self._model is None:
            if not BAKED_WEIGHTS.exists():
                raise FileNotFoundError(
                    f"нет запечённых весов {BAKED_WEIGHTS}. Запустите один раз: "
                    "uv run python tools/bake_world_doors.py"
                )
            from ultralytics import YOLOWorld

            self._model = YOLOWorld(str(BAKED_WEIGHTS))
        return self._model

    def propose(
        self,
        video: Path,
        data: TrackData,
        frame_indices: list[int],
        boxes_by_frame: dict[int, np.ndarray],
    ) -> list[DoorCandidate]:
        import cv2

        model = self._load()
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError(f"не открывается видео: {video}")

        tier1: list[DoorCandidate] = []
        tier2: list[DoorCandidate] = []
        try:
            for idx in frame_indices:
                bbox = boxes_by_frame.get(idx)
                if bbox is None:
                    continue
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok:
                    continue
                x1, y1, x2, y2 = (int(round(v)) for v in bbox)
                x1, y1 = max(x1, 0), max(y1, 0)
                x2, y2 = min(x2, frame.shape[1]), min(y2, frame.shape[0])
                if x2 - x1 < 10 or y2 - y1 < 10:
                    continue
                crop = frame[y1:y2, x1:x2]
                cw, ch = x2 - x1, y2 - y1
                result = model.predict(crop, conf=CONF, verbose=False)[0]
                for box, cls, conf in zip(
                    result.boxes.xyxy.tolist(),
                    result.boxes.cls.tolist(),
                    result.boxes.conf.tolist(),
                ):
                    cls = int(cls)
                    if cls not in _TIER1_IDX and cls not in _TIER2_IDX:
                        continue  # отвлекающий класс — сыграл роль на этапе NMS модели
                    bx0, by0, bx1, by1 = box
                    cand = DoorCandidate(
                        x0=bx0 / cw, y0=by0 / ch, x1=bx1 / cw, y1=by1 / ch,
                        score=float(conf), method="openvocab", frame_idx=idx,
                    )
                    (tier1 if cls in _TIER1_IDX else tier2).append(cand)
        finally:
            cap.release()

        if tier1:
            return tier1
        if tier2:
            log.warning(
                "%s: ярус 1 промптов не дал кандидатов, использую ярус 2", video.name
            )
            return tier2
        log.warning("%s: openvocab не нашёл ни одной двери ни на одном ярусе", video.name)
        return []
