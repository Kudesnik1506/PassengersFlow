"""Открытые детекторы по тексту и по образцу: OWLv2 и GroundingDINO.

Оба отвечают на вопрос, которого нет в COCO: «где здесь дверь автобуса». Оба
тянут веса при первом запуске (от 600 МБ до 1,7 ГБ) и считают на MPS.

Различие, ради которого берутся оба: GroundingDINO спрашивают словами, OWLv2 —
картинкой. Слово «дверь» модель понимает по своему обучающему корпусу, где
дверь чаще всего домашняя или автомобильная; образец же берётся из нашей
собственной разметки соседнего визита. Если победит образец, это прямо укажет,
куда вкладываться дальше — в разметку, а не в подбор формулировок.

Образцы подаёт прогон, по принципу «кроме себя»: дверь того же визита была бы
подсказкой, которой в бою нет.
"""

from __future__ import annotations

import numpy as np

from ..cases import Case
from ..finders import Box, FrameSource, crop_body, ranked

TEXT_PROMPT = "a door of a city bus. bus entrance door. passenger door."
CONF = 0.08  # низкий: отбор по числу дверей делает ансамбль, не порог


def _transformers_missing() -> str | None:
    try:
        import transformers  # noqa: F401
    except ImportError as exc:
        return f"transformers не установлен: {exc}"
    return None


def _device() -> str:
    import torch

    if torch.backends.mps.is_available():
        return "mps"
    return "cuda" if torch.cuda.is_available() else "cpu"


def _to_frame(boxes, offset: tuple[float, float]) -> list[Box]:
    dx, dy = offset
    return [(float(b[0]) + dx, float(b[1]) + dy, float(b[2]) + dx, float(b[3]) + dy)
            for b in boxes]


class OwlV2Finder:
    """OWLv2 по образцу двери, взятому из разметки другого визита."""

    tag = "owlv2"
    title = "OWLv2 по образцу двери"
    MODEL = "google/owlv2-base-patch16-ensemble"

    def __init__(self) -> None:
        # Заполняет прогон: список вырезок дверей с ДРУГИХ визитов.
        self.queries: list[np.ndarray] = []
        self.note = ""
        # Уверенности модели рядом с рамками: по ним из облака кандидатов
        # берутся первые K по числу дверей (bench/counting.py).
        self.scores: list[float] = []
        self._model = None
        self._processor = None

    def available(self) -> str | None:
        missing = _transformers_missing()
        if missing:
            return missing
        try:
            from transformers import Owlv2ForObjectDetection  # noqa: F401
        except ImportError as exc:
            return f"в transformers нет OWLv2: {exc}"
        return None

    def _load(self):
        if self._model is None:
            from transformers import Owlv2ForObjectDetection, Owlv2Processor

            self._processor = Owlv2Processor.from_pretrained(self.MODEL)
            self._model = Owlv2ForObjectDetection.from_pretrained(self.MODEL).to(_device())
            self._model.eval()
        return self._processor, self._model

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        import torch
        from PIL import Image

        processor, model = self._load()
        crop, actual = crop_body(frames.frame(case.frame_idx), case.body_px)
        if crop.size == 0:
            return []
        image = Image.fromarray(crop[:, :, ::-1])

        if self.queries:
            self.note = f"по образцу ({len(self.queries)} шт.), {_device()}"
            boxes, scores = [], []
            for query in self.queries:
                query_image = Image.fromarray(query[:, :, ::-1])
                inputs = processor(images=image, query_images=query_image,
                                    return_tensors="pt").to(_device())
                with torch.no_grad():
                    outputs = model.image_guided_detection(**inputs)
                result = processor.post_process_image_guided_detection(
                    outputs=outputs, threshold=CONF, nms_threshold=0.3,
                    target_sizes=torch.tensor([image.size[::-1]]),
                )[0]
                boxes.extend(result["boxes"].cpu().tolist())
                scores.extend(result["scores"].cpu().tolist())
        else:
            self.note = f"по тексту (образцов не передали), {_device()}"
            inputs = processor(text=[[TEXT_PROMPT]], images=image,
                                return_tensors="pt").to(_device())
            with torch.no_grad():
                outputs = model(**inputs)
            result = processor.post_process_grounded_object_detection(
                outputs=outputs, threshold=CONF,
                target_sizes=torch.tensor([image.size[::-1]]),
            )[0]
            boxes = result["boxes"].cpu().tolist()
            scores = result["scores"].cpu().tolist()

        boxes, self.scores = ranked(_to_frame(boxes, (actual[0], actual[1])), scores)
        return boxes


class GroundingDinoFinder:
    """GroundingDINO по текстовому описанию двери."""

    tag = "groundingdino"
    title = "GroundingDINO по тексту"
    MODEL = "IDEA-Research/grounding-dino-base"

    def __init__(self) -> None:
        self.note = ""
        self.scores: list[float] = []
        self._model = None
        self._processor = None

    def available(self) -> str | None:
        missing = _transformers_missing()
        if missing:
            return missing
        try:
            from transformers import AutoModelForZeroShotObjectDetection  # noqa: F401
        except ImportError as exc:
            return f"в transformers нет zero-shot детекции: {exc}"
        return None

    def _load(self):
        if self._model is None:
            from transformers import AutoModelForZeroShotObjectDetection, AutoProcessor

            self._processor = AutoProcessor.from_pretrained(self.MODEL)
            self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
                self.MODEL).to(_device())
            self._model.eval()
        return self._processor, self._model

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        import torch
        from PIL import Image

        processor, model = self._load()
        crop, actual = crop_body(frames.frame(case.frame_idx), case.body_px)
        if crop.size == 0:
            return []
        image = Image.fromarray(crop[:, :, ::-1])
        self.note = _device()

        inputs = processor(images=image, text=TEXT_PROMPT,
                            return_tensors="pt").to(_device())
        with torch.no_grad():
            outputs = model(**inputs)
        result = processor.post_process_grounded_object_detection(
            outputs, inputs.input_ids, threshold=CONF, text_threshold=0.2,
            target_sizes=[image.size[::-1]],
        )[0]
        boxes, self.scores = ranked(
            _to_frame(result["boxes"].cpu().tolist(), (actual[0], actual[1])),
            result["scores"].cpu().tolist(),
        )
        return boxes
