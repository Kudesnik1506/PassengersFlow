"""Замена бортового номера государственным — то, ради чего заведён портал.

Инструкция заказчика: «Для троллейбусов и трамваев указывается бортовой номер,
а для автобусов государственный». Оператор записывает бортовой всем подряд,
значит замена — наша работа, и делается она единственной возможной дорогой:
`борт → портал → госномер` (решение 026; обратный поиск по госномеру портал не
поддерживает вовсе).

Правила здесь ровно три, и каждое — про то, чего делать НЕЛЬЗЯ:

* не выдумывать. Портал не знает машину — в графе остаётся бортовой, а не
  придуманный номер. В принятом заказчиком файле такие строки есть: «34547 -нет
  в базе»;
* не затирать. Бортовой не исчезает из строки: он единственный ключ, по
  которому спор о машине потом разбирается (решение 026);
* не молчать. Если портал говорит о маршруте не то же, что оператор, это
  расхождение, а не повод подменить одно другим.
"""

from __future__ import annotations

from datetime import datetime

from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.plates import with_plate
from paxcount.portal import NotFound, VehicleInfo

MOMENT = datetime(2026, 9, 10, 7, 2, 17)


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, board_number="1596", state_number=None,
                 route="50", size=VehicleSize.XLARGE, video="запись",
                 operator="Иванов Иван")
    return DeliveryRow(**{**base, **kw})


def answers(result):
    """Портал-заглушка: сеть в тестах не трогаем (решение 016)."""
    def ask(board, date, kind, at):
        assert date == "2026-09-10" and at == MOMENT
        return result
    return ask


def found(**kw) -> VehicleInfo:
    base = dict(board_number="1596", state_number="А000АА00", route="50",
                 carrier="Перевозчик", shift_start=None, shift_end=None)
    return VehicleInfo(**{**base, **kw})


def test_the_portal_plate_fills_the_state_number():
    out, changed = with_plate(row(), MOMENT, answers(found()))
    assert out.state_number == "А000АА00"
    assert out.number == "А000АА00", "в графу заказчика идёт госномер"
    assert changed == frozenset({"number"}), "графу мы изменили, и книга её пометит"


def test_the_board_number_survives_the_replacement():
    """Бортовой — единственный ключ к справочнику, и он остаётся в строке."""
    assert with_plate(row(), MOMENT, answers(found()))[0].board_number == "1596"


def test_a_vehicle_absent_from_the_portal_keeps_its_board_number():
    """Нет в базе — пишем бортовой, а не выдумываем номер.

    Графа при этом не помечается: в ней осталось то же, что записал оператор,
    и красить нечего.
    """
    out, changed = with_plate(row(), MOMENT, answers(NotFound("1596", "нет в базе")))
    assert out.state_number is None
    assert out.number == "1596"
    assert changed == frozenset()
    assert "нет в базе" in " ".join(out.overrides.values())


def test_a_known_state_number_is_not_overwritten():
    """Прочитанный с кадра номер не подменяется: портал тут второе мнение."""
    ours = row(state_number="А001АА00")
    out, changed = with_plate(ours, MOMENT, answers(found()))
    assert out.state_number == "А001АА00"
    assert changed == frozenset(), "ничего не меняли — помечать нечего"


def test_a_trolley_is_not_sent_to_the_portal():
    """У троллейбуса госномера нет вовсе — спрашивать нечего."""
    def refuse(*a, **kw):
        raise AssertionError("портал не должен спрашиваться")
    trolley = row(kind=VehicleKind.TROLLEY, board_number="3144")
    assert with_plate(trolley, MOMENT, refuse)[0].number == "3144"


def test_a_row_without_a_board_number_is_left_alone():
    """Без бортового в портал не с чем идти."""
    def refuse(*a, **kw):
        raise AssertionError("портал не должен спрашиваться")
    assert with_plate(row(board_number=None), MOMENT, refuse)[0].number is None


def test_a_route_the_portal_disputes_is_recorded_not_replaced():
    """Портал говорит другой маршрут — это расхождение, а не замена.

    Маршрут в строке остаётся наш (или оператора): подменить его молча значит
    выдать третье мнение за согласованное. Но и промолчать нельзя — спор уходит
    в книгу графой S, к человеку, который вправе решить (решение 087).
    """
    out, _ = with_plate(row(route="50"), MOMENT, answers(found(route="64")))
    assert out.route == "50"
    assert "64" in out.disagreement_cell


def test_a_successful_substitution_is_not_a_disagreement():
    """«Портал по борту 1596» — происхождение, а не спор.

    Первая боевая сборка залила графу S на 292 строках из 295 именно такими
    пометками. Графа, заполненная почти везде, не говорит ничего.
    """
    out, _ = with_plate(row(), MOMENT, answers(found()))
    assert out.disagreement_cell is None
    assert out.overrides, "происхождение при этом записано — оно нужно для спора"


# ---- Ненайденный госномер объясняется в книге ---------------------------------
#
# Причина отказа портала копилась в `overrides` и до книги не доезжала: заказчик
# видел в графе голый бортовой номер без объяснения. Вопрос «почему в строке 60
# бортовой не заменён» возник именно поэтому — на 295 строках таких девять.
#
# Владелец (18.09): указывать в комментариях. Не в графе расхождений: это не
# спор с оператором, а факт о нашем поиске.


def test_a_board_the_portal_does_not_know_says_so_in_the_comment():
    out, _ = with_plate(row(), MOMENT, answers(NotFound("1596", "нет в базе")))
    assert any("госномер" in note for note in out.notes)
    assert any("1596" in note for note in out.notes), "борт назван: по нему и искали"
    assert out.comment_cell and "госномер" in out.comment_cell


def test_the_explanation_does_not_pretend_to_be_a_table_code():
    """Графа M проверяется по таблице 2; текст не вправе занять место кода."""
    out, _ = with_plate(row(), MOMENT, answers(NotFound("1596", "нет в базе")))
    assert out.comment is None


def test_the_explanation_joins_the_notes_already_there():
    """Разрыв записи и ненайденный номер уживаются в одной клетке."""
    ours = row(notes=("разрыв записи камеры 2 07:26:42-07:44:36",))
    out, _ = with_plate(ours, MOMENT, answers(NotFound("1596", "нет в базе")))
    assert len(out.notes) == 2
    assert "разрыв записи" in out.comment_cell and "госномер" in out.comment_cell


def test_a_portal_that_knows_the_board_but_gives_no_plate_also_says_so():
    out, _ = with_plate(row(), MOMENT, answers(found(state_number=None)))
    assert any("госномер" in note for note in out.notes)


def test_a_found_plate_adds_no_note():
    """Молчание — признак успеха: объяснять нечего, графа остаётся пустой."""
    out, _ = with_plate(row(), MOMENT, answers(found()))
    assert out.notes == ()


def test_a_trolley_is_not_explained_either():
    """У троллейбуса госномера нет по устройству — это не отказ портала."""
    def refuse(*a, **kw):
        raise AssertionError("портал не должен спрашиваться")
    trolley = row(kind=VehicleKind.TROLLEY, board_number="3144")
    assert with_plate(trolley, MOMENT, refuse)[0].notes == ()
