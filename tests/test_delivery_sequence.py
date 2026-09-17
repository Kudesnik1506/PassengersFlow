"""Лента смены: наши расшифровки, вписанные в последовательность оператора.

Бланк заказчика — не выборка посчитанных машин, а ЛЕНТА: инструкция требует
«расшифровываем весь материал», и строка обязана быть у каждого ТС смены.
Наших расшифровок шесть, записей оператора за то же утро — сто с лишним.
Выдать шесть строк значит выдать последовательность, которой на остановке не
было.

Отсюда устройство модуля. Записи оператора — костяк ленты; наши строки встают
на свои места внутри него, а машина, которую оператор пропустил, ВПИСЫВАЕТСЯ
между его записями, а не приписывается в конец.

Главная опасность здесь — две шкалы. Часы камеры и часы оператора на боевой
смене разошлись на семь минут (решение 068), и лента, собранная из двух шкал
разом, переставит машины местами, оставшись при этом правдоподобной на вид.
Поэтому шкала у ленты одна, перевод между ними — измеренная величина, и без
неё слияние не делается вовсе.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from paxcount.delivery.agreement import Agreement
from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.operator import OperatorRecord
from paxcount.delivery.sequence import Decoded, backbone, clock_shift, merge

STOP = "22739"


def record(when: str, board: str, route: str = "26", *, size: str = "Большой",
            kind: str = "Автобус", occupancy: str | None = "А",
            line: int = 2) -> OperatorRecord:
    return OperatorRecord(
        created=datetime.fromisoformat(f"2026-09-10T{when}"), stop=STOP,
        kind=kind, route=route, size=size, board=board, occupancy=occupancy,
        row=line,
    )


def our_row(when: str, board: str | None = None, route: str = "26") -> DeliveryRow:
    moment = datetime.fromisoformat(f"2026-09-10T{when}")
    return DeliveryRow(
        group="697", date="10.09.2026", hours=moment.hour, minutes=moment.minute,
        stop=STOP, kind=VehicleKind.BUS, board_number=board,
        state_number="А000АА00", route=route, size=VehicleSize.LARGE,
        alighted=1, boarded=2, video="запись", operator="Иванов Иван",
    )


def decoded(when: str, board: str | None = None, route: str = "26",
             match: OperatorRecord | None = None) -> Decoded:
    """Наша расшифровка: момент на шкале камеры, строка и итог сверки."""
    return Decoded(
        moment=datetime.fromisoformat(f"2026-09-10T{when}"),
        row=our_row(when, board, route),
        agreement=Agreement(match, frozenset()),
    )


def merged(ours, records, shift):
    return merge(ours, records, shift=shift, group="697", stop=STOP,
                  operator="Иванов Иван")


# --- перевод между часами ---------------------------------------------------

def test_the_clock_shift_is_measured_by_matched_boards():
    """Насколько часы камеры ушли вперёд — считается по найденным машинам.

    Иначе величину пришлось бы задать рукой, а она у каждой смены своя: это
    расхождение двух устройств, а не настройка нашего кода.
    """
    first = record("06:54:59", "1596")
    second = record("06:56:51", "38157")
    shift = clock_shift([decoded("07:02:17", "1596", match=first),
                          decoded("07:04:07", "38157", match=second)])
    assert shift == timedelta(seconds=(7 * 60 + 18 + 7 * 60 + 16) / 2)


def test_the_shift_is_unknown_when_no_vehicle_matched():
    """Ни одной общей машины — величины нет, и выдумывать её нечем."""
    assert clock_shift([decoded("07:02:17", "1596")]) is None


def test_merging_without_a_measured_shift_is_refused():
    """Слияние по двум шкалам разом переставит машины местами молча.

    Ноль вместо неизмеренной поправки выглядит как обычная лента, но ставит
    наши строки на семь минут не туда — то есть между чужими соседями. Такой
    ответ хуже отказа: он не отличим от правильного.
    """
    with pytest.raises(ValueError, match="поправка"):
        merged([decoded("07:02:17", "1596")], [record("06:54:59", "1596")], None)


# --- сама лента -------------------------------------------------------------

def test_a_vehicle_the_operator_missed_is_written_in_between():
    """Пропущенная оператором машина встаёт на своё место, а не в конец.

    Это и есть смысл ленты: заказчик читает её как последовательность
    прибытий. Наша строка, приписанная в конец, говорит о смене неправду,
    будучи при этом правильно заполненной.
    """
    before, after = record("07:00:00", "1111"), record("07:04:00", "3333")
    ours = decoded("07:02:00", "2222")
    order = merged([ours], [before, after], timedelta(0))
    assert [e.row.board_number for e in order] == ["1111", "2222", "3333"]


def test_the_sequence_runs_forward_in_time():
    """Лента упорядочена по времени — на ОДНОЙ шкале, а не по источнику."""
    records = [record("06:54:59", "1596"), record("07:02:07", "38129")]
    ours = [decoded("07:02:17", "1596", match=records[0]),
             decoded("06:56:59", "7326")]
    order = merged(ours, records, timedelta(minutes=7, seconds=18))
    assert [e.moment for e in order] == sorted(e.moment for e in order)


def test_a_matched_vehicle_does_not_appear_twice():
    """Машина, найденная у оператора, — одна строка, а не две.

    Промах здесь тихий: строки отличаются временем на семь минут и выглядят
    как два разных рейса одного борта.
    """
    match = record("06:54:59", "1596")
    order = merged([decoded("07:02:17", "1596", match=match)], [match],
                    timedelta(minutes=7, seconds=18))
    assert len(order) == 1
    assert order[0].decoded


def test_a_row_we_have_not_decoded_carries_no_count():
    """Оператор считает машины, а не людей: счёт по его строке неизвестен.

    Ноль здесь был бы измерением, которого не было, — `None` означает «не
    считали» и доходит до книги как N/A (правило N/A).
    """
    order = merged([], [record("07:00:00", "1111")], timedelta(0))
    assert order[0].row.alighted is None and order[0].row.boarded is None
    assert not order[0].decoded


def test_what_the_operator_knows_about_his_own_row_is_kept():
    """Маршрут, размер, вид и наполненность у его строки берутся у него."""
    order = merged([], [record("07:00:00", "1111", "226", size="Средний")],
                    timedelta(0))
    row = order[0].row
    assert row.route == "226"
    assert row.size is VehicleSize.MEDIUM
    assert row.occupancy is not None and row.occupancy.value == "А"


def test_his_row_is_placed_on_our_clock():
    """Время в ленте — на одной шкале, и это шкала камеры (инструкция).

    «Ориентируемся на время на камере» — правило заказчика. Записать время
    оператора рядом с нашим значило бы смешать шкалы внутри одной графы.
    """
    order = merged([], [record("06:54:59", "1596")],
                    timedelta(minutes=7, seconds=18))
    assert (order[0].row.hours, order[0].row.minutes) == (7, 2)


def test_nothing_disappears_in_the_merge():
    """Число строк — наши плюс его, без общих. Потеря строки тут молчалива."""
    match = record("06:54:59", "1596")
    records = [match, record("07:02:07", "38129"), record("07:04:11", "7126")]
    ours = [decoded("07:02:17", "1596", match=match), decoded("06:56:59", "7326")]
    order = merged(ours, records, timedelta(minutes=7, seconds=18))
    assert len(order) == len(records) + len(ours) - 1


def test_the_operator_row_is_marked_as_not_ours():
    """Строку, которой мы не расшифровывали, книга обязана отличать."""
    match = record("06:54:59", "1596")
    order = merged([decoded("07:02:17", "1596", match=match)],
                    [match, record("07:02:07", "38129")],
                    timedelta(minutes=7, seconds=18))
    assert [e.decoded for e in order] == [True, False]


# --- костяк книги: сутки, а не смена -----------------------------------------
#
# Съёмка идёт тремя окнами с перерывами в часы, и поначалу книга собиралась на
# одно окно — то, где лежат наши расшифровки. Принятый заказчиком файл это
# опроверг: 312 строк за ОДНУ дату, часы с 07 до 20, все три окна в одном файле.
# Инструкция говорит то же словами: «расшифровываем весь материал».

def test_the_book_is_built_for_the_whole_day_not_one_shift():
    """Три окна съёмки — один файл. Взять одно значит выдать треть смены."""
    day = datetime(2026, 9, 10).date()
    records = [record("07:02:07", "1111"), record("12:20:18", "2222"),
                record("17:11:40", "3333")]
    assert len(backbone(records, day)) == 3


def test_another_day_does_not_leak_into_the_book():
    """Файл заказчика — на дату. Соседние сутки в него попадать не должны."""
    day = datetime(2026, 9, 10).date()
    other = OperatorRecord(
        created=datetime(2026, 9, 11, 7, 2, 7), stop=STOP, kind="Автобус",
        route="26", size="Большой", board="9999", occupancy="А", row=9,
    )
    assert backbone([record("07:02:07", "1111"), other], day) == [record("07:02:07", "1111")]


# --- наша правка чужой строки должна быть видна --------------------------------
#
# Оператор четыре раза за сутки поменял местами борт и маршрут и один раз склеил
# их в одно число. Мы это чиним (`operator.repair`) — и до сих пор чинили молча.
# Молчаливая правка чужих данных недопустима вдвойне: заказчик сверяет книгу с
# выгрузкой оператора, видит расхождение и не понимает, чьё оно.

def test_a_repaired_field_is_named_so_the_book_can_show_it():
    """Починенная графа названа поимённо — иначе её нечем пометить."""
    broken = record("07:00:00", "50", "1536")   # борт и маршрут местами
    entry = merged([], [broken], timedelta(0))[0]
    assert entry.row.board_number == "1536" and entry.row.route == "50"
    assert entry.repaired == frozenset({"board", "route"})


def test_an_untouched_row_reports_no_repair():
    """Ничего не чинили — помечать нечего."""
    assert merged([], [record("07:00:00", "1111")], timedelta(0))[0].repaired == frozenset()


def test_the_reason_for_the_repair_travels_with_the_row():
    """Спор о строке разбирается по объяснению, а не по памяти сборщика."""
    entry = merged([], [record("07:00:00", "50", "1536")], timedelta(0))[0]
    assert "поменяны местами" in " ".join(entry.row.overrides.values())


def test_the_entry_carries_its_own_agreement():
    """Итог сверки живёт в строке ленты, а не в карте по объекту.

    Карта «объект строки → сверка» рассыпается, как только строку заменяют:
    подстановка госномера делает НОВЫЙ объект, и книга падала ровно на этом.
    Опознание по личности объекта здесь и не нужно — лента уже знает, откуда
    каждая её строка.
    """
    match = record("06:54:59", "1596")
    entry = merged([decoded("07:02:17", "1596", match=match)], [match],
                    timedelta(minutes=7, seconds=18))[0]
    assert entry.agreement is not None
    assert entry.agreement.record is match
    assert merged([], [record("07:00:00", "1111")], timedelta(0))[0].agreement is None
