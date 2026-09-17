"""Сборка строки поставки: что известно — записываем, чего нет — не выдумываем.

Правило владельца: строка не пропадает из-за того, что счёт не удался. Если
машину видно, но людей посчитать нельзя, в строку уходит всё остальное —
маршрут, номер, время, камера, — а без счёта остаются ровно графы счёта. Так
заказчик и сам сдаёт: в принятом файле `N/A` встречается только в «Вышло» и
«Зашло» (по два раза), а неизвестное в остальных графах остаётся пустым, не
подписывается словом N/A. Поэтому модель хранит `None` («не знаем»), а как это
показать в бланке, решает слой записи.

Коды комментария считаются из разметки дверей, а не пишутся рукой. И считаются
осторожно: автоматически выводится только «посчитать нельзя» (5-8). Коды 1-4
означают «дверь не в кадре, но людей всё же можно досчитать» — а знает это
только человек, который нашёл, как их досчитать (проход под камерой, пустой
салон). Поставить 1-4 самим значит заявить о счёте, которого не было.
"""

# Госномер здесь выдуманный: серия 000 и регион 00 не выдаются, спутать с
# настоящим нельзя. Боевые номера в репозиторий не уходят — кадры с ними не
# публикуются, и номер, переписанный с кадра в файл, остаётся тем же номером.

from __future__ import annotations

from datetime import datetime

import pytest
from paxcount.delivery.model import VehicleKind, VehicleSize
from paxcount.delivery.reconcile import VisitFacts, build_row, comment_code
from paxcount.truth import DoorLayout, DoorLayoutEntry

SEEN = datetime(2026, 9, 10, 7, 2, 6)


def door(n: int, visible: bool = True) -> DoorLayoutEntry:
    box = (100.0 * n, 400.0, 100.0 * n + 80, 800.0) if visible else None
    return DoorLayoutEntry(n_from_nose=n, opening_px=box, in_frame=visible)


def layout(visible: list[int], total: int = 3, size=VehicleSize.LARGE) -> DoorLayout:
    return DoorLayout(
        visit_key="2/07:02:06/1596", camera="2", size=size, orientation="нос-слева",
        video="2026-09-10 - 06-56-06 - 22739_2 - 02", frame_idx=7326,
        frame_size=(1920, 1080),
        doors=[door(n, n in visible) for n in range(1, total + 1)],
    )


def facts(**over) -> VisitFacts:
    base = dict(
        visit_key="2/07:02:06/1596", camera="2", stop_ts=SEEN,
        layout=layout([1, 2, 3]), boarded=3, alighted=0,
        route="50", board_number="1596", state_number="А000АА00",
        kind=VehicleKind.BUS, size=VehicleSize.LARGE, stood_where="второй ряд",
    )
    base.update(over)
    return VisitFacts(**base)


def row(**over):
    return build_row(facts(**over), group="697", stop="22739",
                      video="2026-09-10 - 06-56-06 - 22739_2 - 02", operator="Иванов И.")


# ---- Полная строка ----------------------------------------------------------


def test_complete_visit_fills_every_column():
    result = row()
    assert (result.hours, result.minutes) == (7, 2)
    assert result.route == "50"
    assert result.state_number == "А000АА00"
    assert (result.boarded, result.alighted) == (3, 0)
    assert result.comment is None, "все двери видно — комментарий не нужен"


def test_time_comes_from_the_stop_moment():
    result = row(stop_ts=datetime(2026, 9, 10, 8, 45, 59))
    assert (result.hours, result.minutes) == (8, 45), "секунды отбрасываются"


# ---- Правило владельца: считать нельзя — остальное всё равно пишем ----------


def test_unreadable_count_keeps_everything_else():
    """Людей не посчитали — но маршрут, номер и время в строке остаются."""
    result = row(boarded=None, alighted=None)
    assert result.route == "50" and result.state_number == "А000АА00"
    assert (result.hours, result.minutes) == (7, 2)
    assert result.boarded is None and result.alighted is None
    assert not result.counted


def test_unknown_size_is_not_invented():
    """Размер не виден ни с одной камеры — графа пустая, а не «Большой» на глаз."""
    result = row(size=None)
    assert result.size is None


def test_unknown_route_still_leaves_the_vehicle_number():
    """Инструкция: маршрут не опознан — номер ТС всё равно пишется."""
    result = row(route=None)
    assert result.route is None
    assert result.number == "А000АА00"


def test_row_survives_without_a_layout_at_all():
    """Разметки дверей нет — строка есть, счёта нет, код не выдумывается."""
    result = row(layout=None, boarded=None, alighted=None)
    assert result.comment is None
    assert result.boarded is None


# ---- Коды комментария из разметки -------------------------------------------


@pytest.mark.parametrize("hidden,expected", [
    ([1], 5),        # не попала первая дверь, посчитать нельзя
    ([1, 2], 6),     # не попали первые две (трёхдверный)
    ([3], 7),        # не попала последняя
    ([2, 3], 8),     # не попали последние две
])
def test_codes_five_to_eight_follow_table_two(hidden, expected):
    visible = [n for n in (1, 2, 3) if n not in hidden]
    assert comment_code(layout(visible))[0] == expected


def test_all_doors_visible_means_no_code():
    assert comment_code(layout([1, 2, 3]))[0] is None


def test_articulated_bus_first_half_is_code_six():
    """У четырёхдверного код 6 — про первую половину, то есть двери 1 и 2."""
    code, _ = comment_code(layout([3, 4], total=4, size=VehicleSize.XLARGE))
    assert code == 6


def test_articulated_bus_second_half_is_code_eight():
    code, _ = comment_code(layout([1, 2], total=4, size=VehicleSize.XLARGE))
    assert code == 8


def test_middle_door_hidden_alone_has_no_code_in_the_table():
    """Средняя дверь скрыта, крайние видны — таблица 2 такого случая не знает."""
    code, note = comment_code(layout([1, 3]))
    assert code is None
    assert note and "таблиц" in note.lower()


def test_no_door_visible_at_all_is_a_question_for_the_customer():
    code, note = comment_code(layout([]))
    assert code is None
    assert note and "ни одной" in note.lower()


def test_countable_codes_are_never_assigned_automatically():
    """1-4 значат «дверь не видно, но людей досчитали». Это знает только человек."""
    codes = {comment_code(layout(v))[0] for v in ([1], [2], [3], [1, 2], [2, 3], [1, 3])}
    assert not ({1, 2, 3, 4} & codes)


def test_code_lands_in_the_row():
    """Видна только первая дверь: две последние вне кадра — код 8."""
    result = row(layout=layout([1]), boarded=3, alighted=0)
    assert result.comment == 8


# ---- Порядок и обязательные графы -------------------------------------------


def test_video_and_operator_are_carried_into_the_row():
    result = row()
    assert result.video.endswith("22739_2 - 02")
    assert result.operator == "Иванов И."


def test_row_passes_the_customer_validator():
    from paxcount.delivery.validate import validate

    assert validate([row()]) == []


def test_row_with_partial_data_still_passes_the_validator():
    """Строка без счёта не должна брако́вывать файл — она законна."""
    from paxcount.delivery.validate import validate

    assert validate([row(boarded=None, alighted=None, size=None)]) == []
