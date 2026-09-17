"""Мера качества локализации двери: годится ли предсказание для кропа.

Порог здесь не выбран по вкусу и не взят из практики детекции («IoU 0.5»). Он
выведен из того, ради чего дверь вообще ищется: из проёма режется кроп, и кроп
берётся с запасом `CROP_MARGIN_PX` по обе стороны (решение о запасе — замер на
маршруте 62, где узкий кроп дал недосчёт). Значит, предсказание годится ровно
тогда, когда настоящий проём целиком попадает в кроп, нарезанный по
предсказанию, — иначе человек или модель увидят не ту дверь.

Из этого же следует, что «промах на 30 пикселей» и «промах на 300» — не одно и
то же с точностью до числа: первый не меняет ничего, второй теряет дверь. Одна
метрика IoU это различие сглаживает, поэтому рядом с попаданием считаются и
смещение центра, и перекрытие по горизонтали — но решает попадание.
"""

from __future__ import annotations

from paxcount.bench.score import score_case
from paxcount.settings import CROP_MARGIN_PX

# Эталонный трёхдверный автобус: проёмы по 80 px, шаг 400 px.
REF = [
    (1, (400.0, 500.0, 480.0, 780.0)),
    (2, (800.0, 500.0, 880.0, 780.0)),
    (3, (1200.0, 500.0, 1280.0, 780.0)),
]


def shifted(dx: float) -> list[tuple[float, float, float, float]]:
    return [(x0 + dx, y0, x1 + dx, y1) for _, (x0, y0, x1, y1) in REF]


# ---- Попадание --------------------------------------------------------------


def test_exact_prediction_finds_every_door():
    result = score_case([b for _, b in REF], [b for _, b in REF])
    assert result.found == 3 and result.missed == 0 and result.extras == 0
    assert result.mean_center_error_px == 0.0


def test_shift_within_the_crop_margin_still_finds_the_door():
    """Смещение меньше запаса кропа ничего не теряет — дверь всё ещё в кропе."""
    result = score_case([b for _, b in REF], shifted(CROP_MARGIN_PX - 10))
    assert result.found == 3, "запас кропа покрывает такое смещение"


def test_shift_beyond_the_crop_margin_loses_the_door():
    """Смещение больше запаса уводит проём из кропа — это не найденная дверь."""
    result = score_case([b for _, b in REF], shifted(CROP_MARGIN_PX + 50))
    assert result.found < 3
    assert result.missed > 0


def test_threshold_is_imported_not_invented():
    """Порог — это запас кропа из settings, а не отдельное число метрики."""
    from paxcount.bench import score

    assert score.DEFAULT_MARGIN_PX is CROP_MARGIN_PX


# ---- Назначение один к одному ------------------------------------------------


def test_two_predictions_on_one_door_count_once():
    """Накрыть одну дверь дважды — не двойной зачёт, а попадание и лишний."""
    doubled = [REF[0][1], (410.0, 500.0, 490.0, 780.0)]
    result = score_case([b for _, b in REF], doubled)
    assert result.found == 1
    assert result.extras == 1
    assert result.missed == 2


def test_empty_prediction_misses_everything_without_extras():
    result = score_case([b for _, b in REF], [])
    assert (result.found, result.missed, result.extras) == (0, 3, 0)


def test_prediction_far_from_any_door_is_an_extra():
    """Ложный проём (окно, отражение) считается отдельно от промаха по двери."""
    result = score_case([b for _, b in REF], [b for _, b in REF] + [(50.0, 500.0, 130.0, 780.0)])
    assert result.found == 3 and result.extras == 1


# ---- Что попадает в знаменатель ---------------------------------------------


def test_only_doors_visible_in_the_frame_are_scored():
    """Дверь за краем кадра метод найти не мог — в знаменатель она не идёт."""
    visible = [b for _, b in REF[:2]]
    result = score_case(visible, visible)
    assert result.total == 2 and result.found == 2


# ---- Качество попадания ------------------------------------------------------


def test_center_error_is_reported_in_pixels():
    result = score_case([b for _, b in REF], shifted(20.0))
    assert abs(result.mean_center_error_px - 20.0) < 1e-6


def test_horizontal_overlap_is_reported_alongside():
    """Перекрытие по горизонтали — вторая цифра: попадание бывает грубым."""
    result = score_case([b for _, b in REF], shifted(40.0))
    assert 0.0 < result.mean_iou_x < 1.0
