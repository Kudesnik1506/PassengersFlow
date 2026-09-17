"""Сборка итоговой книги: последовательность маршрутов и наш счёт.

Бланк заказчика — это ЛЕНТА машин, прошедших остановку, строка за строкой по
времени. Собрать её из наших визитов нельзя простой сортировкой: визиты
размечены на разных камерах, а часы камер идут врозь и не на одну величину —
три независимые пары К3↔К2 за одно утро дали −419 / −418 / −428 с
(`delivery.clocks`). Сортировка по «своему» времени поставит машины в порядок,
которого на остановке не было.

Отсюда две проверки последовательности: перевод на общую шкалу обязателен, а
визит, для которого поправки нет, обязан попасть в книгу всё равно — иначе
заказчик недосчитается строки, а недостающая строка видна ему сразу (правило
владельца в `delivery.reconcile`).

Третья проверка — про счёт: «не считали» обязано доехать до книги как `None`, а
не как ноль. Ноль это измерение, `None` — его отсутствие, и слить их значит
выдать несчитанное за совпадение с любым результатом.
"""

from __future__ import annotations

from datetime import datetime

from paxcount.delivery.assemble import Visit, book, book_filename, in_route_order
from paxcount.delivery.clocks import ClockRecord
from paxcount.delivery.model import VehicleKind
from paxcount.delivery.reconcile import VisitFacts

K2_FILE = "2026-09-10 - 06-56-06 - 22739_2 - 02"
K3_FILE = "2026-08-10 - 06-50-46 - 22739_3 - 01"

RECORDS = [
    ClockRecord(camera="2", file=K2_FILE, offset_to_k2_s=0.0, measured_by="ноль шкалы"),
    ClockRecord(camera="3", file=K3_FILE, offset_to_k2_s=-418.0,
                 measured_by="пара событий", date_override="2026-09-10"),
]


def facts(camera: str, when: str, route: str, **kw) -> VisitFacts:
    return VisitFacts(
        visit_key=f"{camera}/{when}/—", camera=camera,
        stop_ts=datetime.fromisoformat(when), route=route,
        kind=VehicleKind.BUS, state_number="А000АА00", **kw,
    )


def test_visits_are_ordered_on_the_common_scale_not_on_their_own_clocks():
    """Часы К3 отстают на 418 с — по своим часам порядок обратный верному.

    На часах К3 машина отмечена в 07:00:30, на К2 другая — в 07:05:00. По
    «своему» времени К3 идёт первой; на общей шкале её момент — 07:07:28, то
    есть она пришла ПОСЛЕ. Книга обязана отдать их в этом порядке.
    """
    visits = [
        Visit(facts("3", "2026-08-10T07:00:30", "26"), K3_FILE),
        Visit(facts("2", "2026-09-10T07:05:00", "62"), K2_FILE),
    ]
    placed = in_route_order(visits, RECORDS)
    assert [p.visit.facts.route for p in placed] == ["62", "26"]
    assert placed[1].reference_ts == datetime(2026, 9, 10, 7, 7, 28)


def test_a_visit_without_a_clock_correction_still_reaches_the_book():
    """Строка не пропадает из-за того, что её нечем поставить на шкалу."""
    visits = [
        Visit(facts("2", "2026-09-10T07:05:00", "62"), K2_FILE),
        Visit(facts("1", "2026-09-10T07:00:00", "26"), "файл без поправки"),
    ]
    placed = in_route_order(visits, RECORDS)
    assert len(placed) == 2, "визит без поправки остаётся в книге"
    assert placed[-1].reference_ts is None, "неразмещённые идут в конец"
    assert placed[-1].problem, "и причина названа, а не замолчана"


def test_not_counted_does_not_become_zero():
    """«Не считали» доезжает до книги как None, а не как ноль."""
    visits = [Visit(facts("2", "2026-09-10T07:05:00", "62", boarded=3), K2_FILE)]
    rows = book(in_route_order(visits, RECORDS), group="1715", stop="22739",
                 operator="Иванов Иван")
    assert rows[0].boarded == 3
    assert rows[0].alighted is None, "ноль был бы измерением, а его не было"


def test_the_book_carries_the_stop_and_the_operator_into_every_row():
    """Графы бланка, одинаковые на всю смену, проставляются сборкой."""
    visits = [
        Visit(facts("3", "2026-08-10T07:00:30", "26"), K3_FILE),
        Visit(facts("2", "2026-09-10T07:05:00", "62"), K2_FILE),
    ]
    rows = book(in_route_order(visits, RECORDS), group="1715", stop="22739",
                 operator="Иванов Иван")
    assert [r.route for r in rows] == ["62", "26"], "порядок книги — порядок ленты"
    assert {r.stop for r in rows} == {"22739"}
    assert {r.group for r in rows} == {"1715"}
    assert {r.operator for r in rows} == {"Иванов Иван"}
    assert [(r.hours, r.minutes) for r in rows] == [(7, 5), (7, 7)], (
        "время в бланке — на общей шкале, а не на часах своей камеры")


def test_the_file_name_takes_the_date_apart_instead_of_swapping_dashes():
    """В графе бланка дата ДД.ММ.ГГГГ, в имени файла — ГГГГ-ММ-ДД.

    Это разные записи одной даты, и заменой точек на дефисы одна в другую не
    превращается: выйдет 10-09-2026 вместо 2026-09-10. Заказчик ищет файл по
    дате, и такой файл он не найдёт — а глазами подмена не видна, потому что
    цифры все на месте.
    """
    visits = [Visit(facts("2", "2026-09-10T07:05:00", "62"), K2_FILE)]
    rows = book(in_route_order(visits, RECORDS), group="1317", stop="22739",
                 operator="Иванов Иван")
    assert rows[0].date == "10.09.2026", "в графе бланка — формат заказчика"
    assert book_filename(rows, group="1317", stop="22739") == \
        "2026-09-10-1317-22739.xlsx"


def test_an_unknown_group_does_not_leave_a_hole_in_the_file_name():
    """Пустая графа в имени даёт «2026-09-10--22739» — это читается как дефект.

    Группа ОП административная, и до её получения книга всё равно собирается.
    Имя файла при этом обязано остаться именем, а не следом отсутствующего
    поля: заказчик судит о поставке в том числе по имени файла.
    """
    visits = [Visit(facts("2", "2026-09-10T07:05:00", "62"), K2_FILE)]
    rows = book(in_route_order(visits, RECORDS), group="", stop="22739",
                 operator="Иванов Иван")
    assert book_filename(rows, group="", stop="22739") == "2026-09-10-22739.xlsx"


# ---- Часы своей камеры едут рядом с общей шкалой ------------------------------
#
# Общая шкала нужна ленте, но проверяющий открывает запись конкретной камеры, и
# перематывать он будет по ЕЁ часам. Замер по борту 7861 показал, чего стоит
# эта разница: на часах К3 машина в 06:58:06, на общей шкале — 07:05:04.
# Перемотав К3 на 07:05:04, проверяющий увидит другую машину.


def test_the_book_keeps_the_reading_of_the_camera_that_saw_the_boarding():
    visits = [Visit(facts("3", "2026-08-10T06:58:06", "26"), K3_FILE)]
    rows = book(in_route_order(visits, RECORDS), group="1317", stop="22739",
                 operator="Иванов Иван")
    assert rows[0].hours == 7 and rows[0].minutes == 5, "в бланке — общая шкала"
    assert rows[0].camera_cell == "К3 06:58:06", "рядом — часы самой камеры"


def test_the_camera_reading_is_not_the_moment_on_the_common_scale():
    """Подставить сюда общее время значит стереть сам смысл графы."""
    visits = [Visit(facts("3", "2026-08-10T06:58:06", "26"), K3_FILE)]
    rows = book(in_route_order(visits, RECORDS), group="1317", stop="22739",
                 operator="Иванов Иван")
    assert rows[0].camera_cell != f"К3 0{rows[0].hours}:0{rows[0].minutes}:04"
