"""Общий контракт сравниваемых методов локализации дверей.

Контракт нарочно у́же и проще, чем `doorprop.DoorProposer`: тот принимает
`TrackData` — кэш детекции, которого у боевых записей нет. Здесь вход ровно
тот, что есть у человека, размечавшего эталон: кадр и рамка кузова на нём.

`find` получает не кадр, а источник кадров: часть методов (временная разность,
и любая будущая, смотрящая на движение) без соседних кадров не работает вовсе,
а таскать ради них весь ролик в память незачем.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

import numpy as np

from .cases import Case

Box = tuple[float, float, float, float]


class FrameSource(Protocol):
    """Доступ к кадрам ролика по номеру и к самому файлу.

    Путь нужен не для красоты: `doorprop` открывает ролик сам и перематывает
    его по номерам кадров — переписывать рабочий код ради стенда значит
    сравнивать не то, что работает в бою.
    """

    path: "Path"

    def frame(self, idx: int) -> np.ndarray:
        ...


class DoorFinder(Protocol):
    """Метод локализации дверей в том виде, в каком его сравнивают с другими."""

    tag: str
    title: str

    def available(self) -> str | None:
        """`None` — метод готов работать. Иначе — чего именно не хватает.

        Причина возвращается строкой, а не исключением: «не установлен» — это
        строка таблицы результатов, а не авария прогона.
        """
        ...

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        """Предложенные дверные проёмы в пикселях КАДРА (не доли рамки)."""
        ...


def to_pixels(fractions: tuple[float, float, float, float], body: Box) -> Box:
    """Доли рамки кузова → пиксели кадра. Формат `doorprop.DoorCandidate`."""
    x0, y0, x1, y1 = body
    w, h = x1 - x0, y1 - y0
    return (x0 + fractions[0] * w, y0 + fractions[1] * h,
            x0 + fractions[2] * w, y0 + fractions[3] * h)


def to_fractions(box: Box, body: Box) -> Box:
    """Пиксели кадра → доли рамки кузова."""
    x0, y0, x1, y1 = body
    w, h = max(x1 - x0, 1e-6), max(y1 - y0, 1e-6)
    return ((box[0] - x0) / w, (box[1] - y0) / h,
            (box[2] - x0) / w, (box[3] - y0) / h)


def crop_body(image: np.ndarray, body: Box) -> tuple[np.ndarray, Box]:
    """Вырезка кузова из кадра и её фактические границы (после обрезки по кадру)."""
    h, w = image.shape[:2]
    x0 = max(0, int(round(body[0])))
    y0 = max(0, int(round(body[1])))
    x1 = min(w, int(round(body[2])))
    y1 = min(h, int(round(body[3])))
    return image[y0:y1, x0:x1], (float(x0), float(y0), float(x1), float(y1))


def ranked(boxes: list[Box], scores: list[float]) -> tuple[list[Box], list[float]]:
    """Кандидаты по убыванию уверенности самой модели.

    Нужно там, где из облака кандидатов берут первые K по числу дверей: без
    этого «первые» значит «в каком порядке их вернула библиотека», а такой
    отбор к качеству метода отношения не имеет.

    Метод, уверенности не сообщающий, не перемешивается: порядок остаётся
    исходным, а веса нулевые — пусть отбор явно окажется произвольным, чем
    тихо выдаст случайную перестановку за ранжирование.
    """
    if len(scores) != len(boxes):
        return list(boxes), [0.0] * len(boxes)
    pairs = sorted(zip(boxes, scores), key=lambda p: -p[1])
    return [b for b, _ in pairs], [float(s) for _, s in pairs]
