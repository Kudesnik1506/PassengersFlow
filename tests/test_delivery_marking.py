"""Что в книге красится жёлтым: правило, а не привычка сборщика.

Пометка отвечает на один вопрос проверяющего: где смотреть кадр. Поэтому она
устроена по величине утверждения, а не по числу незаполненных граф:

* **записи у оператора нет вовсе** — спорна вся строка целиком. Машину видели
  только мы, второго свидетельства у неё нет ни в одном поле, и красить одну
  графу из пятнадцати значило бы указать не туда;
* **запись есть, разошлось одно поле** — красится ровно эта клетка. Залить из-за
  неё всю машину значит обесценить пометку: проверяющий станет искать спор в
  четырнадцати графах, где его нет;
* **всё сошлось** — не красится ничего.

Время занимает две графы бланка (часы и минуты), поэтому одно расхождение по
времени даёт две клетки: половина момента на белом фоне читалась бы как
«минуты сошлись», чего сверка не утверждала.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from paxcount.delivery.agreement import Agreement, agree
from paxcount.delivery.fill import COLUMNS
from paxcount.delivery.marking import marks_for
from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.operator import OperatorRecord


def record(**kw) -> OperatorRecord:
    base = dict(created=datetime(2026, 9, 10, 6, 54, 59), stop="22739",
                 kind="Автобус", route="50", size="Особо большой", board="1596",
                 occupancy="А", row=3)
    return OperatorRecord(**{**base, **kw})


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, board_number="1596", state_number="А000АА00",
                 route="50", size=VehicleSize.XLARGE, alighted=0, boarded=5,
                 video="запись", operator="Иванов Иван")
    return DeliveryRow(**{**base, **kw})


SHIFT = timedelta(minutes=7, seconds=18)


def test_a_vehicle_the_operator_never_recorded_is_marked_whole():
    """Машину видели только мы — спорна вся строка, а не какая-то её графа."""
    assert marks_for(Agreement(None, frozenset())) == set(COLUMNS)


def test_a_single_disagreeing_field_marks_only_its_own_cell():
    """Разошлось одно поле — красится одна клетка, остальные четырнадцать нет."""
    result = agree(row(route="26"), [record()], SHIFT)
    assert result.mismatched == {"route"}
    assert marks_for(result) == {"H"}


def test_a_time_disagreement_marks_both_hours_and_minutes():
    """Момент лежит в двух графах: пометить одну значит сказать лишнее."""
    result = agree(row(hours=7, minutes=20), [record()], SHIFT)
    assert marks_for(result) == {"C", "D"}


def test_a_row_that_agrees_is_not_marked_at_all():
    """Сошлось — пометки нет. Иначе цвет перестаёт что-либо значить."""
    assert marks_for(agree(row(), [record()], SHIFT)) == set()


def test_a_missing_occupancy_is_not_a_disagreement():
    """Наполненность мы не считаем вовсе (решение 032) — спорить в ней нечем."""
    result = agree(row(), [record(occupancy=None)], SHIFT)
    assert "I" not in marks_for(result)


# ---- Наша правка чужой строки ------------------------------------------------
#
# Строка оператора, которую мы не расшифровывали, не красится: она целиком его.
# Но если мы в ней что-то ИСПРАВИЛИ — эта клетка уже наша, и она обязана быть
# видна. Иначе заказчик, сверяя книгу с выгрузкой, найдёт расхождение и не
# поймёт, чьё оно: наша это правка или его собственная опечатка.

def test_a_field_we_repaired_is_marked():
    from paxcount.delivery.marking import marks_for_repair
    assert marks_for_repair(frozenset({"board", "route"})) == {"G", "H"}


def test_nothing_repaired_marks_nothing():
    from paxcount.delivery.marking import marks_for_repair
    assert marks_for_repair(frozenset()) == set()


def test_a_duplicate_press_marks_the_whole_row():
    """Повтор — утверждение обо всей строке, а не о какой-то её графе.

    Лишняя здесь не ячейка, а сама машина: её записали дважды. Покрасить одну
    графу значило бы сказать, что спорна она, — и отправить искать ошибку в
    номере или маршруте, где всё верно.
    """
    from paxcount.delivery.marking import marks_for_duplicate
    assert marks_for_duplicate() == set(COLUMNS)


# ---- Наш счёт в чужой строке -------------------------------------------------
#
# Строка машины, которая нашлась у оператора, выглядит как все остальные: вид,
# маршрут, размер, борт — его. Наше в ней только «Вышло» и «Зашло», и в ленте из
# трёхсот строк эти две клетки не найти. А ведь ради них всё и делается.

def test_our_count_is_marked_in_the_operators_row():
    """Счёт — наше измерение, и в книге он обязан быть виден."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert marks_for_our_measurement(row()) == {"K", "L"}


def test_a_row_without_a_count_is_not_marked():
    """Не считали — помечать нечего: N/A не измерение."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert marks_for_our_measurement(row(alighted=None, boarded=None)) == set()


def test_the_comment_code_is_ours_too():
    """Код таблицы 2 выведен из нашей разметки дверей, а не взят у оператора."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "M" in marks_for_our_measurement(row(comment=7))
