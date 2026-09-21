"""Раскладка строк книги — тоже правка заказчика, и пересборка её помнит.

Реестр правок держит значения клеток. Этого мало: заказчик удаляет розовые
повторы, переставляет машины, пришедшие в другом порядке, дописывает номер в
строку, где мы его не прочли, и заводит строку на машину, которую не видели
ни оператор, ни мы. Ни одно из этих действий не значение клетки, и прежде
каждая пересборка молча откатывала их все: вернула удалённую строку, вернула
прежний порядок.

Правка раскладки узнаётся тем же способом, что и правка клетки, — разницей с
эталоном: чего в книге нет против копии прошлой сборки, то убрал человек.
"""

from __future__ import annotations

from datetime import date, datetime

from paxcount.delivery import layout, manual
from paxcount.delivery.model import DeliveryRow, VehicleKind
from paxcount.delivery.sequence import OPERATOR, Entry

DAY = date(2026, 9, 10)


def cells(hh: int, mm: int, number: str = "", **kw) -> dict[str, str]:
    return {"C": str(hh), "D": str(mm), "G": number, "F": "Автобус", **kw}


def entry(hh: int, mm: int, number: str | None, **kw) -> Entry:
    row = DeliveryRow(group="697", date="10.09.2026", hours=hh, minutes=mm,
                       stop="22739", kind=VehicleKind.BUS, state_number=number,
                       **kw)
    return Entry(moment=datetime(2026, 9, 10, hh, mm), row=row, origin=OPERATOR)


def numbers(entries: list[Entry]) -> list[str | None]:
    return [e.row.number for e in entries]


def laid(entries, book, baseline):
    plan = layout.found(book, baseline)
    return layout.apply(entries, plan, day=DAY, group="697", stop="22739",
                         operator="").entries


# --- удалённая строка ---------------------------------------------------------

def test_a_row_the_customer_deleted_stays_deleted():
    """Удалённый повтор не возвращается следующей сборкой.

    Боевой случай: заказчик убрал пятнадцать розовых строк, и первая же
    пересборка вернула их все.
    """
    base = [cells(8, 9, "А1"), cells(8, 9, "А1"), cells(8, 10, "Б2")]
    book = [cells(8, 9, "А1"), cells(8, 10, "Б2")]
    fresh = [entry(8, 9, "А1"), entry(8, 9, "А1"), entry(8, 10, "Б2")]
    assert numbers(laid(fresh, book, base)) == ["А1", "Б2"]


def test_the_edits_of_a_deleted_row_are_not_a_loss():
    """Правка в удалённой строке не повод отказать в записи книги.

    Реестр отказывается писать книгу, если правка потеряла строку (рубеж 2).
    Строку удалил сам заказчик — его правкам в ней держаться не за что, и
    отказ здесь заблокировал бы книгу навсегда.
    """
    base = [cells(8, 9, "А1"), cells(8, 10, "Б2")]
    book = [cells(8, 10, "Б2")]
    plan = layout.found(book, base)
    edits = [manual.Edit(key=("8", "9", "А1"), repeat=0, column="K", value="3"),
             manual.Edit(key=("8", "10", "Б2"), repeat=0, column="K", value="1")]
    assert [e.value for e in layout.surviving(edits, plan)] == ["1"]


# --- перестановка -------------------------------------------------------------

def test_a_swap_is_kept():
    """Машины, которые заказчик поменял местами, стоят в его порядке.

    Порядок прибытия — главный признак приёмки (решение 028), и заказчик видит
    его по камере лучше, чем оператор по кнопке.
    """
    base = [cells(8, 12, "А1"), cells(8, 13, "Б2"), cells(8, 13, "В3"), cells(8, 14, "Г4")]
    book = [cells(8, 12, "А1"), cells(8, 13, "В3"), cells(8, 13, "Б2"), cells(8, 14, "Г4")]
    fresh = [entry(8, 12, "А1"), entry(8, 13, "Б2"), entry(8, 13, "В3"), entry(8, 14, "Г4")]
    assert numbers(laid(fresh, book, base)) == ["А1", "В3", "Б2", "Г4"]


def test_an_untouched_book_changes_nothing():
    base = [cells(8, 12, "А1"), cells(8, 13, "Б2")]
    fresh = [entry(8, 12, "А1"), entry(8, 13, "Б2")]
    plan = layout.found(base, base)
    assert plan.empty
    assert numbers(laid(fresh, base, base)) == ["А1", "Б2"]


# --- дописанный номер ---------------------------------------------------------

def test_a_number_written_into_a_nameless_row_renames_it():
    """Строка без номера, куда заказчик вписал номер, — та же строка.

    Боевой случай: две строки цепочки К1 без номера получили от заказчика
    госномер, наполненность, размер и счёт. Приметы у них сменились, и прежде
    они выглядели удалёнными, а его строки — чужими: пропало бы всё сразу.
    """
    base = [cells(8, 39, "А1"), cells(8, 40, "", P="К2 08:47:44", H="N/A")]
    book = [cells(8, 39, "А1"), cells(8, 40, "Н146КК198", P="К2 08:47:44", H="3", K="9")]
    # Камера у безымянной строки уже проставлена: без неё приметы неполны, и
    # сборка применяет раскладку после поиска записи (`with_footage`).
    fresh = [entry(8, 39, "А1"),
             entry(8, 40, None, camera="2", camera_ts=datetime(2026, 9, 10, 8, 47, 44))]
    plan = layout.found(book, base)
    out = layout.apply(fresh, plan, day=DAY, group="697", stop="22739", operator="")
    assert numbers(out.entries) == ["А1", "Н146КК198"]
    kept = {(e.column, e.value) for e in plan.edits}
    assert ("H", "3") in kept and ("K", "9") in kept, "его клетки в строке — правки"


# --- новая строка -------------------------------------------------------------

def test_a_row_the_customer_added_is_kept_in_its_place():
    """Строка, заведённая заказчиком, стоит там, где он её поставил."""
    base = [cells(8, 47, "А1"), cells(8, 49, "Б2")]
    book = [cells(8, 47, "А1"), cells(8, 48, "Н180ВМ198", H="263", K="17"),
            cells(8, 49, "Б2")]
    fresh = [entry(8, 47, "А1"), entry(8, 49, "Б2")]
    plan = layout.found(book, base)
    out = layout.apply(fresh, plan, day=DAY, group="697", stop="22739", operator="")
    assert numbers(out.entries) == ["А1", "Н180ВМ198", "Б2"]
    assert out.entries[1].row.route == "263"
    assert ("K", "17") in {(e.column, e.value) for e in plan.edits}


# --- память -------------------------------------------------------------------

def test_the_layout_is_remembered_next_to_the_edits(tmp_path):
    """Раскладка живёт в том же реестре и не затирает правки клеток."""
    path = tmp_path / "правки.json"
    manual.save(path, [manual.Edit(key=("8", "9", "А1"), repeat=0, column="K", value="3")])
    plan = layout.found([cells(8, 10, "Б2")], [cells(8, 9, "А1"), cells(8, 10, "Б2")])
    layout.save(path, plan)
    assert layout.load(path).deleted == plan.deleted
    assert len(manual.load(path)) == 1


def test_a_remembered_deletion_survives_a_book_that_agrees():
    """Эталон сравнялся с книгой — удаление помнит реестр, а не разница.

    После записи книга и эталон одинаковы, и разница больше ничего не
    показывает. Без реестра вторая пересборка вернула бы удалённую строку.
    """
    first = layout.found([cells(8, 10, "Б2")], [cells(8, 9, "А1"), cells(8, 10, "Б2")])
    second = layout.found([cells(8, 10, "Б2")], [cells(8, 10, "Б2")])
    plan = layout.merged(first, second)
    fresh = [entry(8, 9, "А1"), entry(8, 10, "Б2")]
    out = layout.apply(fresh, plan, day=DAY, group="697", stop="22739", operator="")
    assert numbers(out.entries) == ["Б2"]


def test_saving_the_edits_keeps_the_layout(tmp_path):
    """Правки клеток пишутся в реестр после раскладки — и не стирают её.

    В сборке раскладка сохраняется первой, правки клеток последними: реестр,
    переписанный ими целиком, забыл бы удалённые строки при первой же сборке.
    """
    path = tmp_path / "правки.json"
    plan = layout.found([cells(8, 10, "Б2")], [cells(8, 9, "А1"), cells(8, 10, "Б2")])
    layout.save(path, plan)
    manual.save(path, [])
    assert layout.load(path).deleted == plan.deleted


def test_a_renamed_row_takes_the_route_the_customer_wrote():
    """Дописанный маршрут — часть строки, а не только клетка поверх неё.

    Боевой случай: заказчик вписал в безымянную строку номер и маршрут 3.
    Маршрут остался только правкой клетки, строка внутри сборки была без
    маршрута — и сборка приписала в комментарий «маршрут не опознан».
    """
    base = [cells(8, 40, "", P="К2 08:47:44", H="N/A")]
    book = [cells(8, 40, "Н146КК198", P="К2 08:47:44", H="3")]
    fresh = [entry(8, 40, None, camera="2", camera_ts=datetime(2026, 9, 10, 8, 47, 44))]
    out = layout.apply(fresh, layout.found(book, base), day=DAY, group="697",
                        stop="22739", operator="")
    assert out.entries[0].row.route == "3"


def test_an_empty_cell_in_the_customers_row_is_his_too():
    """В строке, которую завёл заказчик, пустая клетка — его решение.

    Боевой случай: он оставил комментарий пустым, а сборка вписала туда код
    таблицы 2 по кадру. Строка целиком его, и «пустота — тоже решение».
    """
    base = [cells(8, 47, "А1")]
    book = [cells(8, 47, "А1"), cells(8, 48, "Н180ВМ198", H="263", M="")]
    plan = layout.found(book, base)
    assert ("M", "") in {(e.column, e.value) for e in plan.edits}
