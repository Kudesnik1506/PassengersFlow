"""Пункт 17 инструкции: маршрут не виден — «N/A», а номер ТС в комментарий.

Дословно: «Если не виден номер маршрута, то тоже ставим N/A, а в пустой графе
справа в таблице, на этой же строчке пишем бортовой номер или госномер
транспорта. Но количество транспорта с неопознанным номером маршрута не должно
превышать 5 %».

Где эта «пустая графа справа» — видно по принятому заказчиком образцу: правее
графы O в нём не заполнено ничего, а графа комментария заполнена на шести
строках из 312. Значит она и есть.

Порог 5 % — правило приёмки, а не совет: работа, где неопознанных больше,
заказчиком не принимается.
"""

from __future__ import annotations

from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.reconcile import with_unnamed_route
from paxcount.delivery.xlsx import row_values


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, board_number="1596", state_number="А000АА00",
                 size=VehicleSize.LARGE)
    return DeliveryRow(**{**base, **kw})


def cells(r: DeliveryRow) -> dict[str, str]:
    return dict(zip("ABCDEFGHIJKLMNOPQRS", row_values(r)))


def test_an_unnamed_route_is_written_as_na():
    assert cells(with_unnamed_route(row(route=None)))["H"] == "N/A"


def test_the_vehicle_number_moves_into_the_comment():
    """Чтобы машину можно было опознать, когда маршрут не прочитан."""
    out = with_unnamed_route(row(route=None))
    assert out.comment_cell and "А000АА00" in out.comment_cell


def test_a_trolley_names_its_board_number():
    """У рельсового и троллейбуса в графе номера стоит бортовой — он и идёт."""
    out = with_unnamed_route(row(route=None, kind=VehicleKind.TROLLEY,
                                  state_number=None, board_number="3144"))
    assert "3144" in out.comment_cell


def test_a_named_route_is_left_alone():
    out = with_unnamed_route(row(route="26"))
    assert cells(out)["H"] == "26"
    assert out.comment_cell is None


def test_the_note_does_not_displace_a_table_code():
    """Код таблицы 2 и номер уживаются: графа несёт и то и другое."""
    out = with_unnamed_route(row(route=None, comment=7))
    assert out.comment == 7
    assert "А000АА00" in out.comment_cell and out.comment_cell.startswith("7")


def test_a_row_without_any_number_says_so_instead_of_inventing_one():
    out = with_unnamed_route(row(route=None, board_number=None, state_number=None))
    assert out.comment_cell is None, "нечего называть — и нечего писать"


def test_na_comes_back_as_an_unknown_route_not_as_a_route_named_na(tmp_path):
    """Пересборка читает прошлую книгу: «N/A» — это незнание, а не маршрут."""
    from paxcount.delivery.xlsx import read_rows, write_rows

    path = tmp_path / "книга.xlsx"
    write_rows(path, [with_unnamed_route(row(route=None))])
    assert read_rows(path)[0].route is None
