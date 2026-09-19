"""Код таблицы 2 по числу дверей в кадре — то, чего геометрия сказать не может.

`reconcile.clipped_code` ставил код по одному признаку: кузов упёрся в край
кадра. Замер показал, чего это стоит — из 239 обрезанных стоянок у 155 за
кадром остаётся меньше 5 % кузова, то есть все двери на месте, а код всё равно
ставился. Полтораста ложных кодов в книге заказчика, и отличить их от верных
нельзя.

Здесь код выводится из того, что таблица 2 и спрашивает: сколько у машины
дверей всего и сколько попало в кадр. Разницу даёт модель по одному кадру,
направление задано заказчиком (нос справа), а какой конец за краем — геометрия.

Три запрета, на которых держится модуль:

* **не угадывать по одному прогону** — решение 024: один ответ модели нечем
  проверить. Нужно строгое большинство независимых;
* **не ставить код при разногласии** — пустая графа честнее правдоподобной:
  ложный код в книге неотличим от верного;
* **не верить числу дверей на слово** — оно сверяется с размером ТС по
  таблице 3, и расхождение уходит заказчику отдельным файлом.
"""

from __future__ import annotations

import pytest

from paxcount.delivery.edgedoors import (
    NOSE_CUT, TAIL_CUT, DoorAnswer, agreed, code_from_answer, cut_end,
    hidden_doors, size_mismatch,
)
from paxcount.delivery.model import VehicleSize
from paxcount.doorprop.layout import NOSE_LEFT, NOSE_RIGHT

FRAME = (1920, 1080)


def test_a_clipped_body_whose_doors_all_fit_gets_no_code():
    """Ради этого всё и делается: обрез кузова — ещё не пропавшая дверь.

    Именно здесь геометрия ошибалась 155 раз из 239: край кадра задет, значит
    код 5 или 7. Модель видит, что все три двери на месте, и кода нет.
    """
    code, why = code_from_answer(DoorAnswer(total=3, in_frame=3), NOSE_CUT)
    assert code is None
    assert why is None, "нечего объяснять: случай не для таблицы 2"


def test_the_hidden_doors_are_counted_from_the_cut_end():
    """Двери нумеруются от носа, а прячутся с того конца, который срезан."""
    assert hidden_doors(total=3, in_frame=2, end=NOSE_CUT) == [1]
    assert hidden_doors(total=3, in_frame=2, end=TAIL_CUT) == [3]
    assert hidden_doors(total=4, in_frame=2, end=NOSE_CUT) == [1, 2]
    assert hidden_doors(total=4, in_frame=2, end=TAIL_CUT) == [3, 4]


@pytest.mark.parametrize("total,in_frame,end,expected", [
    (3, 2, NOSE_CUT, 5),          # не попала первая
    (3, 2, TAIL_CUT, 7),          # не попала последняя
    (3, 1, NOSE_CUT, 6),          # не попала первая половина
    (3, 1, TAIL_CUT, 8),          # не попала последняя половина
    (4, 3, NOSE_CUT, 5),
    (4, 2, NOSE_CUT, 6),
    (4, 2, TAIL_CUT, 8),
    (2, 1, NOSE_CUT, 5),
])
def test_the_code_follows_the_table(total, in_frame, end, expected):
    assert code_from_answer(DoorAnswer(total, in_frame), end)[0] == expected


def test_a_vehicle_with_no_door_in_frame_is_a_question_for_a_human():
    """Таблица 2 не описывает «не видно ни одной» — код не подбирается."""
    code, why = code_from_answer(DoorAnswer(total=3, in_frame=0), NOSE_CUT)
    assert code is None
    assert why and "ни одной" in why


def test_more_doors_in_frame_than_the_vehicle_has_is_refused():
    """Ответ модели бывает несвязным — считать по нему нельзя."""
    code, why = code_from_answer(DoorAnswer(total=3, in_frame=4), NOSE_CUT)
    assert code is None
    assert why and "4" in why and "3" in why


def test_one_run_is_an_opinion_not_an_agreement():
    """Решение 024: одиночный ответ модели нечем проверить."""
    answer, why = agreed([DoorAnswer(3, 2)])
    assert answer is None
    assert why and "один" in why.lower()


def test_the_majority_decides_and_the_average_is_never_taken():
    answer, why = agreed([DoorAnswer(3, 2), DoorAnswer(3, 2), DoorAnswer(3, 3)])
    assert answer == DoorAnswer(total=3, in_frame=2)
    assert why == ""


def test_runs_that_scatter_leave_the_cell_empty():
    """Пустая графа честнее правдоподобной: ложный код неотличим от верного."""
    answer, why = agreed([DoorAnswer(3, 1), DoorAnswer(3, 2), DoorAnswer(3, 3)])
    assert answer is None
    assert why and "попало в кадр" in why


def test_a_tie_is_not_a_majority():
    """Двое против двоих — не большинство, а спор: `Counter` тут решал бы жребием."""
    answer, _ = agreed([DoorAnswer(3, 2), DoorAnswer(3, 2),
                         DoorAnswer(3, 3), DoorAnswer(3, 3)])
    assert answer is None


def test_the_cut_end_comes_from_the_camera_not_from_the_model():
    """Направление задано заказчиком: нос справа. У модели оно не спрашивается."""
    assert cut_end((300.0, 200.0, 1920.0, 900.0), FRAME, NOSE_RIGHT)[0] == NOSE_CUT
    assert cut_end((0.0, 200.0, 1500.0, 900.0), FRAME, NOSE_RIGHT)[0] == TAIL_CUT
    assert cut_end((0.0, 200.0, 1500.0, 900.0), FRAME, NOSE_LEFT)[0] == NOSE_CUT


def test_a_body_inside_the_frame_has_no_cut_end():
    assert cut_end((300.0, 200.0, 1500.0, 900.0), FRAME, NOSE_RIGHT) == (None, None)


def test_a_body_cut_on_both_sides_is_a_question_for_a_human():
    end, why = cut_end((0.0, 200.0, 1920.0, 900.0), FRAME, NOSE_RIGHT)
    assert end is None
    assert why and "обеих" in why


def test_an_unset_direction_is_refused_not_guessed():
    end, why = cut_end((0.0, 200.0, 1500.0, 900.0), FRAME, None)
    assert end is None
    assert why and "направление" in why


def test_the_door_count_is_checked_against_the_size():
    """Заказчик (18.09): расхождение с размером — отдельным файлом с пояснением."""
    note = size_mismatch(2, VehicleSize.LARGE)
    assert note and "2" in note and "3" in note and "Большой" in note


def test_a_door_count_that_matches_the_size_is_silent():
    assert size_mismatch(3, VehicleSize.LARGE) is None


def test_a_rail_size_is_not_checked_against_doors():
    """У вагонов размер — это вагоны, а не двери (таблица 3 их не описывает)."""
    assert size_mismatch(3, VehicleSize.TWO_CARS) is None
    assert size_mismatch(3, None) is None
