"""VLPart: единственный найденный детектор, у которого «дверь автобуса» — класс.

Остальные открытые детекторы знают автобус целиком и не знают его частей. В
словаре VLPart `bus:door` присутствует явно (PascalPart, класс 21) — поэтому он
и был первым кандидатом по итогам поиска.

Ставится он не пакетом: detectron2 из pip, сам VLPart — репозиторий с весами.
Отсюда три отдельные проверки готовности вместо одной: в таблице должно стоять,
чего именно не хватает, а не общее «не установлен».

Словарь задаётся способом самого VLPart — `custom vocabulary`: список имён
классов прогоняется через его текстовый энкодер и подменяет классификатор
модели. Рядом с дверью нарочно перечислены окно, колесо и корпус: они не
нужны в ответе, но участвуют в подавлении — та же уловка, что в нашем методе A,
где отвлекающие классы отделяют дверь от окна.
"""

from __future__ import annotations

import contextlib
import os
import sys

from ...settings import MODELS_DIR
from ..cases import Case
from ..finders import Box, FrameSource, crop_body, ranked

REPO_DIR = MODELS_DIR / "VLPart"
WEIGHTS = MODELS_DIR / "vlpart_r50_lvis_paco_pascalpart_partimagenet.pth"
CONFIG = REPO_DIR / "configs" / "joint" / "r50_lvis_paco_pascalpart_partimagenet.yaml"
CONF = 0.1

# Дверь первой: её индекс 0 и проверяется в ответе. Остальные — отвлекающие.
VOCABULARY = ["bus:door", "bus:window", "bus:wheel", "bus:body", "bus:headlight"]


@contextlib.contextmanager
def _inside_repo():
    """Конфигурация VLPart ссылается на `datasets/metadata/*.npy` относительно
    корня репозитория — строить модель приходится, стоя в нём."""
    before = os.getcwd()
    os.chdir(REPO_DIR)
    try:
        yield
    finally:
        os.chdir(before)


class VLPartFinder:
    """`bus:door` как класс детектора частей."""

    tag = "vlpart"
    title = "VLPart: класс bus:door"

    def __init__(self) -> None:
        self.note = ""
        self.scores: list[float] = []
        self._predictor = None

    def available(self) -> str | None:
        try:
            import detectron2  # noqa: F401
        except ImportError as exc:
            return f"detectron2 не установлен: {exc}"
        if not CONFIG.exists():
            return (f"нет репозитория VLPart в {REPO_DIR} — "
                    f"git clone --depth 1 https://github.com/facebookresearch/VLPart {REPO_DIR}")
        if not WEIGHTS.exists():
            return (f"нет весов {WEIGHTS.name} — скачать из релизов VLPart: "
                    "r50_lvis_paco_pascalpart_partimagenet.pth (~176 МБ)")
        return None

    def _load(self):
        if self._predictor is not None:
            return self._predictor

        # Абсолютные пути считаются ДО смены каталога: внутри репозитория
        # относительный `models/VLPart/...` разрешился бы от него самого и
        # удвоился.
        repo = REPO_DIR.resolve()
        config = CONFIG.resolve()
        weights = WEIGHTS.resolve()

        for path in (str(repo), str(repo / "demo")):
            if path not in sys.path:
                sys.path.insert(0, path)

        import torch
        from detectron2.config import get_cfg
        from detectron2.engine import DefaultPredictor

        with _inside_repo():
            from predictor import get_clip_embeddings, reset_cls_test
            from vlpart.config import add_vlpart_config

            cfg = get_cfg()
            add_vlpart_config(cfg)
            cfg.merge_from_file(str(config))
            cfg.MODEL.WEIGHTS = str(weights)
            cfg.MODEL.ROI_HEADS.SCORE_THRESH_TEST = CONF
            # MPS у detectron2 не поддержан официально; пробуем и честно
            # откатываемся, вместо того чтобы падать посреди прогона.
            for device in ("mps", "cpu") if torch.backends.mps.is_available() else ("cpu",):
                cfg.MODEL.DEVICE = device
                try:
                    predictor = DefaultPredictor(cfg)
                    reset_cls_test(predictor.model, get_clip_embeddings(VOCABULARY))
                    self.note = f"{device}, словарь: {VOCABULARY[0]}"
                    self._predictor = predictor
                    return predictor
                except Exception as exc:  # noqa: BLE001 — причина уходит в таблицу
                    last = exc
            raise RuntimeError(f"модель не строится ни на mps, ни на cpu: {last}")

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        predictor = self._load()
        crop, actual = crop_body(frames.frame(case.frame_idx), case.body_px)
        if crop.size == 0:
            return []
        instances = predictor(crop)["instances"].to("cpu")
        boxes: list[Box] = []
        scores: list[float] = []
        for box, cls, score in zip(instances.pred_boxes.tensor.tolist(),
                                    instances.pred_classes.tolist(),
                                    instances.scores.tolist()):
            if int(cls) != 0:  # 0 — bus:door, см. VOCABULARY
                continue
            boxes.append((box[0] + actual[0], box[1] + actual[1],
                           box[2] + actual[0], box[3] + actual[1]))
            scores.append(float(score))
        boxes, self.scores = ranked(boxes, scores)
        return boxes
