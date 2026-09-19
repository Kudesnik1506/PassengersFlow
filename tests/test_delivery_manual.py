"""Правка заказчика главнее нашего расчёта.

Прежний порядок (решение 079) держал главным наше значение: клетку, которую
заполняет сборка, перенос не трогал. Заказчик отменил его: книгу ведёт он,
сборка заполняет её однажды, а дальше только уточняет — своё несогласие пишет
в графу S, а клетку оставляет как есть.

Отличить правку человека от нашего же прошлого значения можно только по
эталону: рядом с книгой лежит копия того, что записала прошлая сборка. Чем
книга разошлась с копией — то вписал человек.
"""

from __future__ import annotations

from paxcount.delivery import manual
from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize

KEY = ("7", "5", "А000АА00")


def row(**kw) -> DeliveryRow:
    base = dict(group="1317", date="10.09.2026", hours=7, minutes=5, stop="22739",
                 kind=VehicleKind.BUS, state_number="А000АА00", route="26",
                 size=VehicleSize.LARGE, video="запись", operator="Иванов Иван")
    return DeliveryRow(**{**base, **kw})


def cells(**kw) -> dict[str, str]:
    """Клетки одной строки книги: приметы плюс то, что задано тестом."""
    base = {"C": "7", "D": "5", "G": "А000АА00"}
    return {**base, **kw}


def edit(column: str, value: str, repeat: int = 0) -> manual.Edit:
    return manual.Edit(key=KEY, repeat=repeat, column=column, value=value)


def test_a_cell_the_customer_changed_is_an_edit():
    """Книга разошлась с нашей копией — значит там побывал человек."""
    diff = manual.found([cells(H="225")], [cells(H="N/A")])
    assert diff.edits == [edit("H", "225")]


def test_a_cell_we_changed_ourselves_is_not_an_edit():
    """Наше новое значение с эталоном не сравнивается вовсе.

    Иначе всякое улучшение расчёта выглядело бы правкой человека и
    закреплялось бы навсегда — ровно то, чего боялось решение 079.
    """
    assert manual.found([cells(H="225")], [cells(H="225")]).edits == []


def test_an_erased_cell_is_an_edit():
    """Пустота — тоже решение заказчика (его слова), и она закрепляется."""
    assert manual.found([cells(M="")], [cells(M="7")]).edits == [edit("M", "")]


def test_a_date_saved_as_text_is_not_an_edit():
    """Графа B во всей книге разошлась не рукой, а редактором.

    Книга, сохранённая не Excel, кладёт в дату строку JS вместо числа. Это
    порча представления, а не правка: закрепив её, мы навсегда положим в книгу
    заказчика текст вместо даты, и его сводная перестанет считать по датам.
    """
    text = "Thu Sep 10 2026 03:00:00 GMT+0300 (Москва, стандартное время)"
    diff = manual.found([cells(B=text)], [cells(B="46275")])
    assert diff.edits == []
    assert diff.spoiled == {"B": 1}


def test_our_own_columns_are_never_captured():
    """P, Q, R, S — наши приписки справа от бланка, и спорить в них не о чем.

    Пустота в них — наше «сказать нечего», а не чужая работа (решение 079).
    Закрепив правку в графе расхождений, мы заморозили бы устаревший спор.
    """
    assert manual.found([cells(S="не согласен")], [cells(S="")]).edits == []


def test_an_edit_lands_on_top_of_our_value():
    """Главное правило: в клетке остаётся то, что вписал заказчик."""
    done = manual.applied([row(route="26")], [edit("H", "225")])
    assert done.values[2]["H"] == "225"


def test_our_value_moves_to_the_dispute_column():
    """Мы не правим, а уточняем: наше несогласие уходит в графу S."""
    done = manual.applied([row(route="26")], [edit("H", "225")])
    assert "26" in (done.rows[0].disagreement_cell or "")


def test_an_edit_we_agree_with_says_nothing():
    """Совпало — спорить не о чем, графа S остаётся чистой."""
    done = manual.applied([row(route="225")], [edit("H", "225")])
    assert done.rows[0].disagreement_cell is None


def test_an_erased_cell_stays_erased():
    """Стёртую клетку сборка не заполняет заново, но говорит, что хотела."""
    done = manual.applied([row(comment=7)], [edit("M", "")])
    assert done.values[2]["M"] == ""
    assert "7" in (done.rows[0].disagreement_cell or "")


def test_an_edit_clears_the_yellow_mark():
    """Жёлтое — наше (запрет 11). В клетке заказчика нашего больше нет."""
    done = manual.applied([row(route="26")], [edit("H", "225")])
    assert manual.unmarked({2: {"H", "C"}}, done.values) == {2: {"C"}}


def test_an_edit_whose_row_vanished_is_named():
    """Приметы поехали — правку некуда положить, и молчать об этом нельзя."""
    done = manual.applied([row(state_number="В111ВВ11")], [edit("H", "225")])
    assert done.lost == [edit("H", "225")]
    assert done.values == {}


def test_a_dropped_edit_lets_our_value_through():
    """Снять закрепление можно только руками заказчика — ключом при запуске."""
    kept = manual.without([edit("H", "225")], ["H2"], [row()])
    assert kept == []


def test_dropping_an_unknown_cell_is_refused():
    """Промах по клетке — не «ничего не сняли», а повод остановиться."""
    try:
        manual.without([edit("H", "225")], ["K9"], [row()])
    except LookupError as why:
        assert "K9" in str(why)
    else:
        raise AssertionError("снятие несуществующей правки прошло молча")


def test_the_register_survives_a_round_trip(tmp_path):
    """Реестр — память о правках: эталон их найдёт один раз, помнить должен он."""
    path = tmp_path / "правки.json"
    manual.save(path, [edit("H", "225"), edit("M", "")])
    assert manual.load(path) == [edit("H", "225"), edit("M", "")]


def test_a_fresh_edit_replaces_the_remembered_one():
    """Заказчик передумал — в реестре остаётся последнее слово, не первое."""
    kept = manual.merged([edit("H", "225")], [edit("H", "226")])
    assert kept == [edit("H", "226")]


def test_without_a_baseline_the_empty_cells_are_still_captured():
    """Эталона нет — правку в наших графах не распознать, но пустые наши клетки
    заведомо заполнены заказчиком, и терять их нельзя (прежний перенос)."""
    assert manual.carried([cells(I="Б")], [row()]) == [edit("I", "Б")]


def test_a_cell_we_never_fill_is_not_a_dispute():
    """«Вышло» мы не заполняем вовсе (запрет 13) — спорить там не о чем.

    Первый боевой прогон залил графу S четырнадцатью строками «у нас пусто,
    оставлено ваше 1»: графа, заполненная везде, не значит ничего (§11).
    """
    done = manual.applied([row()], [edit("K", "1")])
    assert done.rows[0].disagreement_cell is None


# --- Строка без номера: приметы расширяются ---------------------------------


def test_two_rows_without_a_number_are_told_apart():
    """Машина, которую никто не записал, может остаться безымянной.

    Приметы строки — часы, минуты и номер ТС. Две безымянные строки в одну
    минуту дают одинаковые приметы, и перенос правок заказчика начинает путать
    их между собой — на всей книге, а не только на них. Поэтому там, где
    номера нет, четвёртой приметой становится графа P: камера и её часы.
    """
    первая = cells(C="7", D="12", G="", P="К2 07:18:04")
    вторая = cells(C="7", D="12", G="", P="К2 07:18:49")
    assert manual.key_of_cells(первая) != manual.key_of_cells(вторая)


def test_a_row_with_a_number_keeps_its_three_marks():
    """Прежние приметы не трогаются: реестр правок уже собран на них.

    В `out/правки-697-22739.json` записи трёхэлементные. Расширь мы приметы
    всем строкам — ни одна прежняя правка не нашла бы своей клетки.
    """
    assert manual.key_of_cells(cells(C="6", D="52", G="Р753ОТ198",
                                      P="К2 07:00:00")) == ("6", "52", "Р753ОТ198")


# --- Клетка, которой у нас нет, принадлежит заказчику всегда -------------------
#
# Боевая потеря: заказчик проставил наполненность в трёх строках, а сборка их
# стёрла. Разница с эталоном молчала — значение успело попасть в эталон прошлым
# переносом, и правка стала невидимой ровно там, где её и надо было хранить.

def test_a_cell_we_leave_empty_belongs_to_the_customer_even_with_a_baseline():
    """Наполненность мы не считаем вовсе (решение 032): что стоит в графе I —
    его и только его, с эталоном или без.

    Разница с эталоном такую клетку не ловит: значение в эталоне стоит то же,
    и человека в ней «не видно». Видно другое, и этого достаточно: графу
    заполняет не наш расчёт.
    """
    diff = manual.theirs([cells(I="Б")], [cells(I="Б")], [row()])
    assert edit("I", "Б") in diff.edits


def test_a_cell_we_do_fill_is_still_judged_by_the_baseline():
    """Графу, которую заполняем мы, по-прежнему судит только эталон.

    Иначе всякое наше значение, уцелевшее в книге, закрепилось бы правкой
    заказчика и навсегда заморозило бы расчёт — то, чего боялось решение 079.
    """
    diff = manual.theirs([cells(H="26")], [cells(H="26")], [row()])
    assert diff.edits == []
