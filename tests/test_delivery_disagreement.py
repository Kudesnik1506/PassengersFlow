"""Графа S: всё, с чем мы не согласны, — в книге, а не в наших потрохах.

Расхождения копились в `DeliveryRow.overrides` и никуда не доезжали:
`xlsx.row_values` их не читает. Получалось, что мы заметили описку оператора,
записали её себе и промолчали — а заказчик потом спрашивает, почему в строке 60
стоит бортовой номер вместо государственного.

Владелец (18.09): отдельного файла расхождений не будет, всё в одной книге,
графой после Q и R. Туда идёт то, что мы не вправе исправить молча: кто ошибся
— оператор в размере или наша модель в счёте — из строки не видно.

Графа наша целиком, как P, Q и R: у оператора её нет, и метится она за
происхождение, а не за спор (принцип 9).
"""

from __future__ import annotations

from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, size=VehicleSize.LARGE, board_number="1596")
    return DeliveryRow(**{**base, **kw})


def test_nothing_to_argue_about_leaves_the_cell_empty():
    """Пустая графа — норма: несогласие бывает на девяти строках из трёхсот."""
    assert row().disagreement_cell is None


def test_a_disagreement_names_the_column_it_is_about():
    """«Портал говорит 64» без слова «маршрут» читателю ничего не говорит."""
    cell = row(disagreements={"маршрут": "портал говорит 64, в строке 50"}).disagreement_cell
    assert cell == "маршрут: портал говорит 64, в строке 50"


def test_several_disagreements_share_one_cell():
    """Строка бывает спорна не в одном месте, и терять второе нельзя."""
    cell = row(disagreements={
        "размер": "дверей насчитано 2, размер «Большой» по таблице 3 означает 3",
        "маршрут": "портал говорит 64, в строке 50",
    }).disagreement_cell
    assert "дверей насчитано 2" in cell
    assert "портал говорит 64" in cell
    assert cell.count("; ") == 1, "разделитель тот же, что в графе комментария"


def test_the_order_does_not_jump_between_rebuilds():
    """Книга пересобирается по многу раз за день; прыгающий порядок — ложная правка."""
    overrides = {"маршрут": "б", "размер": "а", "госномер": "в"}
    first = row(overrides=overrides).disagreement_cell
    second = row(overrides=dict(reversed(list(overrides.items())))).disagreement_cell
    assert first == second


def test_the_cell_survives_the_round_trip_through_the_book():
    """Пересборка читает прошлую книгу; графа не должна дробиться при чтении."""
    cell = row(disagreements={"размер": "по таблице 3 пары не бывает"}).disagreement_cell
    assert "; " not in cell
