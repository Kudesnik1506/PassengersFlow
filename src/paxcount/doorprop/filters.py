"""Общий постфильтр кандидатов на дверь — один для обоих методов.

Оба метода (пиксельный и open-vocabulary) выдают сырые прямоугольники в
долях bbox ТС. Дальше они проходят одну и ту же гигиену: геометрия похожа на
дверь, кандидаты не дублируют друг друга, порядок слева направо.
"""

from __future__ import annotations

from . import DoorCandidate

MIN_WIDTH = 0.05
MAX_WIDTH = 0.30
MIN_HEIGHT = 0.35
MIN_ASPECT = 1.3  # h / w
MAX_Y1 = 0.80  # низ проёма не выше этой доли высоты bbox — иначе не достаёт до порога
MIN_Y0, MAX_Y0 = 0.10, 0.55
NMS_IOU = 0.3
X_OVERLAP_SUPPRESS = 0.5
EDGE_MARGIN = 0.03  # доля ширины bbox — кандидат у самого обрезанного края бракуется
MAX_DOORS = 4


def _shape_ok(c: DoorCandidate) -> bool:
    w, h = c.x1 - c.x0, c.y1 - c.y0
    if w <= 0 or h <= 0:
        return False
    if not (MIN_WIDTH <= w <= MAX_WIDTH):
        return False
    if h < MIN_HEIGHT:
        return False
    if h / w < MIN_ASPECT:
        return False
    if c.y1 < MAX_Y1:
        return False
    if not (MIN_Y0 <= c.y0 <= MAX_Y0):
        return False
    return True


def _iou_x(a: DoorCandidate, b: DoorCandidate) -> float:
    """IoU по оси x — двери одна над другой не бывает, различаем только по x."""
    lo = max(a.x0, b.x0)
    hi = min(a.x1, b.x1)
    inter = max(0.0, hi - lo)
    union = (a.x1 - a.x0) + (b.x1 - b.x0) - inter
    return inter / union if union > 0 else 0.0


def _nms(candidates: list[DoorCandidate]) -> list[DoorCandidate]:
    ordered = sorted(candidates, key=lambda c: c.score, reverse=True)
    kept: list[DoorCandidate] = []
    for cand in ordered:
        if all(
            _iou_x(cand, k) < NMS_IOU and _iou_x(cand, k) < X_OVERLAP_SUPPRESS
            for k in kept
        ):
            kept.append(cand)
    return kept


def keep_doors(
    candidates: list[DoorCandidate], truncated_left: bool, truncated_right: bool
) -> list[DoorCandidate]:
    """Фильтрует, дедуплицирует и сортирует кандидатов слева направо.

    ``truncated_left``/``truncated_right`` — обрезан ли bbox ТС этим краем
    кадра на опорных кадрах: кандидат впритык к обрезанному краю глушится,
    потому что там же может обрываться и настоящая дверь, и просто корпус.
    """
    shaped = [c for c in candidates if _shape_ok(c)]
    if truncated_left:
        shaped = [c for c in shaped if c.x0 > EDGE_MARGIN]
    if truncated_right:
        shaped = [c for c in shaped if c.x1 < 1.0 - EDGE_MARGIN]
    kept = _nms(shaped)
    kept.sort(key=lambda c: c.x0)
    return kept[:MAX_DOORS]
