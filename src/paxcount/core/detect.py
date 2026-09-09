"""Детекция и трекинг. Общая обвязка над ultralytics для всех бэкендов."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import torch

PERSON_CLASSES = {"person"}
VEHICLE_CLASSES = {"bus", "truck", "train", "car"}


def pick_device(requested: str | None = None) -> str:
    if requested:
        return requested
    if torch.backends.mps.is_available():
        return "mps"
    if torch.cuda.is_available():
        return "cuda"
    return "cpu"


@dataclass
class Tracks:
    """Треки одного кадра, разложенные на людей и ТС."""

    person_ids: np.ndarray
    person_boxes: np.ndarray  # (N, 4) xyxy
    person_conf: np.ndarray
    vehicle_ids: np.ndarray
    vehicle_boxes: np.ndarray
    vehicle_conf: np.ndarray
    vehicle_names: list[str]


class Detector:
    """YOLO + трекер.

    ``tracker`` — yaml трекера ultralytics. botsort.yaml включает компенсацию
    движения камеры (GMC), что нужно для съёмки с рук; для статичной камеры
    bytetrack.yaml быстрее (решение №4 в плане).
    """

    def __init__(
        self,
        weights: str | None = None,
        device: str | None = None,
        conf: float = 0.25,
        imgsz: int = 960,
        tracker: str = "botsort.yaml",
        vehicle_classes: set[str] | None = None,
    ) -> None:
        from ultralytics import YOLO

        from ..settings import DETECTOR

        # Умолчание живёт в settings: детектор не заводит собственную копию
        # знания о том, какая модель считается основной.
        self.model = YOLO(weights or DETECTOR.weights)
        self.device = pick_device(device)
        self.conf = conf
        self.imgsz = imgsz
        self.tracker = tracker
        self.vehicle_classes = vehicle_classes or VEHICLE_CLASSES
        self.names: dict[int, str] = self.model.names

    def reset(self) -> None:
        """Сбрасывает состояние трекера между видео."""
        if hasattr(self.model, "predictor") and self.model.predictor is not None:
            if hasattr(self.model.predictor, "trackers"):
                for t in self.model.predictor.trackers:
                    t.reset()

    def track(self, frame: np.ndarray) -> Tracks:
        res = self.model.track(
            frame,
            persist=True,
            tracker=self.tracker,
            conf=self.conf,
            imgsz=self.imgsz,
            device=self.device,
            verbose=False,
        )[0]

        empty = (np.zeros(0, dtype=int), np.zeros((0, 4)), np.zeros(0))
        if res.boxes is None or res.boxes.id is None:
            return Tracks(*empty, *empty, [])

        ids = res.boxes.id.cpu().numpy().astype(int)
        boxes = res.boxes.xyxy.cpu().numpy()
        confs = res.boxes.conf.cpu().numpy()
        clss = res.boxes.cls.cpu().numpy().astype(int)
        labels = [self.names[c] for c in clss]

        p = np.array([lbl in PERSON_CLASSES for lbl in labels], dtype=bool)
        v = np.array([lbl in self.vehicle_classes for lbl in labels], dtype=bool)
        if len(labels) == 0:
            p = v = np.zeros(0, dtype=bool)

        return Tracks(
            person_ids=ids[p],
            person_boxes=boxes[p],
            person_conf=confs[p],
            vehicle_ids=ids[v],
            vehicle_boxes=boxes[v],
            vehicle_conf=confs[v],
            vehicle_names=[lbl for lbl, keep in zip(labels, v) if keep],
        )
