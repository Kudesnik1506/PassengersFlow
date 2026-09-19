"""Перепроверка оператора: что мы заметили, но не вправе исправить.

Оператор стоит на остановке и заполняет вид и размер ТС на глаз, по машине,
которая уже отъезжает. Проверить его есть чем:

* **таблица 3** говорит, какие пары «вид + размер» вообще существуют, и прямо
  называет невозможные («Таких нет», «Таких нет в СПб»);
* **двери в кадре** дают размер независимо от него (`edgedoors`, таблица 3
  наоборот), а размер, в свою очередь, сужает вид.

Ни одна из этих проверок графу не правит. Кто ошибся — оператор в виде,
оператор в размере или наша модель в счёте дверей, — из строки не видно, а
молча подменить чужую запись догадкой значит выдать третье мнение за
согласованное. Расхождение уходит в графу S (решение 087), и разбирает его
человек.
"""

from __future__ import annotations

from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.review import with_checks


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, size=VehicleSize.LARGE, board_number="1596")
    return DeliveryRow(**{**base, **kw})


def test_a_row_that_holds_together_gets_nothing():
    assert with_checks(row(), doors_seen=3).disagreement_cell is None


def test_a_pair_the_table_forbids_is_recorded():
    """«Троллейбус + Малый» таблица 3 помечает «Таких нет» — это описка."""
    out = with_checks(row(kind=VehicleKind.TROLLEY, size=VehicleSize.SMALL))
    assert out.disagreement_cell and "Троллейбус" in out.disagreement_cell


def test_doors_that_disagree_with_the_size_are_recorded():
    """Насчитали две двери, а в графе «Большой» — это три двери по таблице 3."""
    out = with_checks(row(size=VehicleSize.LARGE), doors_seen=2)
    assert "2" in out.disagreement_cell and "Большой" in out.disagreement_cell


def test_doors_can_dispute_the_kind_itself():
    """Размер, измеренный нами, сужает вид: одна дверь — не троллейбус.

    Это и есть перепроверка вида, которой раньше не было ни у одной строки без
    нашей расшифровки.
    """
    out = with_checks(row(kind=VehicleKind.TROLLEY, size=None), doors_seen=1)
    assert out.disagreement_cell and "Троллейбус" in out.disagreement_cell


def test_a_kind_the_doors_allow_is_not_disputed():
    out = with_checks(row(kind=VehicleKind.TROLLEY, size=None), doors_seen=3)
    assert out.disagreement_cell is None


def test_nothing_is_corrected_silently():
    """Главное правило: мы называем расхождение, а решает человек."""
    out = with_checks(row(kind=VehicleKind.TROLLEY, size=VehicleSize.SMALL),
                       doors_seen=3)
    assert out.kind is VehicleKind.TROLLEY
    assert out.size is VehicleSize.SMALL


def test_disagreements_already_in_the_row_are_kept():
    """Портал спорил о маршруте раньше — его довод не вытесняется нашим."""
    out = with_checks(row(disagreements={"маршрут": "портал говорит 64, в строке 50"},
                           size=VehicleSize.LARGE), doors_seen=2)
    assert "портал говорит 64" in out.disagreement_cell
    assert "дверей" in out.disagreement_cell


def test_without_a_door_count_only_the_pair_is_judged():
    """Дверей не считали — спорить о размере нечем, и молчание тут честнее."""
    assert with_checks(row(size=VehicleSize.LARGE)).disagreement_cell is None


def test_an_unknown_size_is_not_judged_against_the_doors():
    """Пустая графа размера — не расхождение: оператор её и не заполнял."""
    out = with_checks(row(size=None, kind=VehicleKind.BUS), doors_seen=3)
    assert out.disagreement_cell is None
