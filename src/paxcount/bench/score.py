"""Мера качества локализации двери: годится ли предсказание для нарезки кропа.

Порог не выбран по вкусу и не взят из практики детекции («IoU 0.5»). Он выведен
из назначения: из проёма режется кроп с запасом `CROP_MARGIN_PX` по обе стороны
(запас — замер маршрута 62, где узкий кроп дал недосчёт). Предсказание годится
ровно тогда, когда настоящий проём целиком попадает в кроп, нарезанный по
предсказанию. Всё остальное — потеря двери, как бы красиво ни выглядел IoU.

Смещение центра и перекрытие по горизонтали считаются рядом, но не решают:
промах на 30 px и промах на 300 px различаются не величиной, а тем, осталась
дверь в кропе или нет, и усреднённый IoU это различие сглаживает.

Сравнение идёт по горизонтали. Двери различаются положением вдоль борта;
вертикаль у всех дверей одного кузова почти одна и та же, и включать её в меру
значило бы мерить в основном точность верхней границы проёма — величину, от
которой счёт людей не зависит.
"""

from __future__ import annotations

from dataclasses import dataclass

from ..settings import CROP_MARGIN_PX

# Импорт, а не копия (запрет 9): это тот же запас, с которым режется кроп.
DEFAULT_MARGIN_PX = CROP_MARGIN_PX

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class DoorMatch:
    """Одна эталонная дверь и то, что ей сопоставлено (или не сопоставлено)."""

    reference_px: Box
    predicted_px: Box | None
    center_error_px: float | None
    iou_x: float

    @property
    def hit(self) -> bool:
        return self.predicted_px is not None


@dataclass(frozen=True)
class CaseScore:
    """Итог одного метода на одном визите."""

    matches: tuple[DoorMatch, ...]
    extra_px: tuple[Box, ...]

    @property
    def total(self) -> int:
        return len(self.matches)

    @property
    def found(self) -> int:
        return sum(m.hit for m in self.matches)

    @property
    def missed(self) -> int:
        return self.total - self.found

    @property
    def extras(self) -> int:
        return len(self.extra_px)

    @property
    def mean_center_error_px(self) -> float | None:
        errors = [m.center_error_px for m in self.matches if m.center_error_px is not None]
        return sum(errors) / len(errors) if errors else None

    @property
    def mean_iou_x(self) -> float | None:
        ious = [m.iou_x for m in self.matches if m.hit]
        return sum(ious) / len(ious) if ious else None


def _iou_x(a: Box, b: Box) -> float:
    inter = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    union = (a[2] - a[0]) + (b[2] - b[0]) - inter
    return inter / union if union > 0 else 0.0


def _covers(prediction: Box, reference: Box, margin_px: float) -> bool:
    """Останется ли настоящий проём внутри кропа, нарезанного по предсказанию."""
    return (prediction[0] - margin_px <= reference[0]
            and reference[2] <= prediction[2] + margin_px)


def _center_x(box: Box) -> float:
    return (box[0] + box[2]) / 2


def score_case(
    reference: list[Box],
    predicted: list[Box],
    margin_px: float = DEFAULT_MARGIN_PX,
) -> CaseScore:
    """Сопоставляет предсказанные проёмы эталонным один к одному.

    `reference` — только двери, видимые в кадре: дверь за краем кадра метод
    найти не мог, и держать её в знаменателе значит наказывать за съёмку.

    Назначение один к одному, а не «накрыто ли хоть чем-нибудь»: два проёма,
    предложенные на одну дверь, — это одно попадание и один лишний прямоугольник,
    иначе метод, засыпающий кадр кандидатами, получал бы лучшую оценку, чем
    метод, называющий ровно три двери.
    """
    pairs = [
        (_iou_x(p, r), ri, pi)
        for ri, r in enumerate(reference)
        for pi, p in enumerate(predicted)
        if _covers(p, r, margin_px)
    ]
    pairs.sort(key=lambda t: (-t[0], t[1], t[2]))

    taken_ref: dict[int, int] = {}
    taken_pred: set[int] = set()
    for _iou, ri, pi in pairs:
        if ri in taken_ref or pi in taken_pred:
            continue
        taken_ref[ri] = pi
        taken_pred.add(pi)

    matches = []
    for ri, ref in enumerate(reference):
        pi = taken_ref.get(ri)
        if pi is None:
            matches.append(DoorMatch(reference_px=ref, predicted_px=None,
                                      center_error_px=None, iou_x=0.0))
            continue
        pred = predicted[pi]
        matches.append(DoorMatch(
            reference_px=ref, predicted_px=pred,
            center_error_px=abs(_center_x(pred) - _center_x(ref)),
            iou_x=_iou_x(pred, ref),
        ))

    extras = tuple(p for i, p in enumerate(predicted) if i not in taken_pred)
    return CaseScore(matches=tuple(matches), extra_px=extras)
