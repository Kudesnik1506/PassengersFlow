"""Выгрузка оператора: подсказка, а не готовый список.

Заказчик сказал прямо: «Оператор мог что-то пропустить. Нужно это иметь в виду.
И в таком случае добавить этот транспорт». Значит выгрузка — не эталон, по
которому нас проверяют, а черновик, который мы уточняем. Проверять её надо
соответственно.

Замер по настоящей выгрузке (остановка 22739, 2026-09-10) показал три вида
порчи, и каждый из них здесь закреплён тестом:

* **дубли** — 50 записей из 292 сделаны дважды за секунды (в одном случае
  секунда в секунду). Отдать их заказчику значит отдать 50 лишних строк;
* **перепутанные поля** — 4 строки, где бортовой номер попал в графу маршрута
  и наоборот, плюс одна с лишней цифрой в борте;
* **склейка** — маршрут `501596` при борте `1596`: оператор вписал в маршрут
  ещё и бортовой номер.

Ремонт перестановки делается без обращения к порталу намеренно: формы полей
взаимно однозначны (борт — 4–5 цифр, маршрут — 1–3 знака), и строка `борт 225 /
маршрут 38201` не имеет второго прочтения. А вот `382252` — шесть цифр, и
кандидатов у неё два; такую строку чинить молча нельзя, её место в ручном
разборе.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from paxcount.delivery.operator import (
    DUPLICATE_WINDOW_S,
    OperatorRecord,
    drop_duplicates,
    repair,
    shifts,
)


def rec(**over) -> OperatorRecord:
    base = dict(created=datetime(2026, 9, 10, 7, 0, 0), stop="22739", kind="Автобус",
                route="26", size="Большой", board="38157", occupancy="А", row=2)
    base.update(over)
    return OperatorRecord(**base)


# ---- Форма полей -----------------------------------------------------------


@pytest.mark.parametrize("board,route,ok", [
    ("38157", "26", True),
    ("7861", "263", True),
    ("1504", "2В", True),      # маршрут с буквой — законная форма
    ("382252", "226", False),  # шесть цифр в борте
    ("50", "1536", False),     # поля переставлены
])
def test_well_formed_is_recognised(board, route, ok):
    assert rec(board=board, route=route).well_formed is ok


# ---- Ремонт ----------------------------------------------------------------


def test_swapped_fields_are_repaired():
    """`борт 225 / маршрут 38201` читается однозначно — и только так."""
    fixed, note = repair(rec(board="225", route="38201"))
    assert (fixed.board, fixed.route) == ("38201", "225")
    assert note and "местами" in note


def test_route_glued_with_board_is_split():
    """Маршрут `501596` при борте `1596` — это маршрут 50 плюс вписанный борт."""
    fixed, note = repair(rec(board="1596", route="501596"))
    assert (fixed.board, fixed.route) == ("1596", "50")
    assert note and "склей" in note


def test_ambiguous_board_is_not_repaired_silently():
    """У `382252` два прочтения — 38225 и 38252. Молча выбрать нельзя."""
    fixed, note = repair(rec(board="382252"))
    assert fixed.board == "382252", "испорченное значение сохранено как есть"
    assert note is None
    assert not fixed.well_formed, "строка остаётся видимой для ручного разбора"


def test_healthy_record_is_never_touched():
    fixed, note = repair(rec())
    assert fixed == rec() and note is None


# ---- Дубли -----------------------------------------------------------------


def test_second_press_within_the_window_is_a_duplicate():
    first = rec(created=datetime(2026, 9, 10, 7, 5, 37))
    again = rec(created=datetime(2026, 9, 10, 7, 5, 39), row=3)
    result = drop_duplicates([first, again])
    assert result.kept == [first] and result.dropped == [again]


def test_operator_correcting_himself_is_still_a_duplicate():
    """62 в 08:29:31 и 64 в 08:29:35 у одного борта — это правка, не вторая машина.

    Таких пар в боевой выгрузке четыре. Сверка по паре «борт и маршрут»
    считала их разными визитами и оставляла обе строки — то есть ровно то
    лишнее, за что заказчик бракует файл.
    """
    first = rec(created=datetime(2026, 9, 10, 8, 29, 31), route="62")
    fixed = rec(created=datetime(2026, 9, 10, 8, 29, 35), route="64", row=3)
    result = drop_duplicates([first, fixed])
    assert result.kept == [first], "время берётся от первого нажатия"
    assert result.dropped == [fixed]
    assert result.disputed == [(first, fixed)], "расхождение предъявлено, а не решено"


def test_clean_duplicate_is_not_disputed():
    """Повтор без расхождений спорить не о чем."""
    first = rec(created=datetime(2026, 9, 10, 7, 5, 37))
    again = rec(created=datetime(2026, 9, 10, 7, 5, 39), row=3)
    assert drop_duplicates([first, again]).disputed == []


def test_same_bus_hours_later_is_a_new_visit():
    """Машина возвращается на маршрут — это второй визит, а не опечатка."""
    first = rec(created=datetime(2026, 9, 10, 7, 5, 0))
    later = rec(created=datetime(2026, 9, 10, 13, 5, 0), row=9)
    result = drop_duplicates([first, later])
    assert result.kept == [first, later] and result.dropped == []


def test_different_buses_in_one_minute_both_stay():
    """46 % машин делят минуту с соседней — слипнуться они не должны."""
    a = rec(created=datetime(2026, 9, 10, 7, 5, 0), board="38157")
    b = rec(created=datetime(2026, 9, 10, 7, 5, 8), board="38290", row=3)
    assert drop_duplicates([a, b]).kept == [a, b]


def test_the_window_is_measured_against_the_same_bus_not_against_the_flow():
    """Окно меряется по ТОЙ ЖЕ машине, а не по потоку разных.

    Раньше здесь стояло `< 80` — медиана интервала между прибытиями. Величина
    не та: снятие дублей идёт по бортовому номеру, и две разные машины не
    столкнутся никогда, как бы плотно они ни шли. Значение имеет другое
    расстояние — между двумя НАСТОЯЩИМИ заездами одного борта.

    Замер по суткам на 22739 (после починки полей): 51 пара одного борта лежит
    в пределах 63 секунд, следующая — через два часа. Между ними пусто, и порог
    обязан стоять внутри этой пустой полосы, а не на её краю.
    """
    assert 63 < DUPLICATE_WINDOW_S < 2 * 3600


def test_a_repeat_press_just_over_a_minute_is_still_a_duplicate():
    """Автобус не возвращается на остановку через минуту.

    Борт 38427, строки 527 и 537: 08:35:52 и 08:36:55, тот же маршрут 226,
    наполненность исправлена с А на Б. Шестьдесят три секунды — это второе
    нажатие, а не второй приезд, и прежнее окно в минуту его пропускало.
    """
    first = rec(board="38427", route="226", occupancy="А", row=527)
    second = rec(created=first.created + timedelta(seconds=63), board="38427",
                  route="226", occupancy="Б", row=537)
    result = drop_duplicates([first, second])
    assert [r.row for r in result.kept] == [527]
    assert [r.row for r in result.dropped] == [537]


def test_duplicates_are_found_by_the_repaired_number_not_the_typed_one():
    """Личность машины известна только ПОСЛЕ починки полей.

    Оператор путает борт с маршрутом. Пока сверка идёт по напечатанному, две
    записи одной машины выглядят как разные борта — а две разные машины, у
    которых в графу борта попал один и тот же номер маршрута, наоборот,
    сливаются в одну. Обе ошибки тихие.
    """
    clean = rec(board="38201", route="225", row=1)
    swapped = rec(created=clean.created + timedelta(seconds=10),
                   board="225", route="38201", row=2)
    result = drop_duplicates([clean, swapped])
    assert [r.row for r in result.kept] == [1], "это одна машина, а не две"


# ---- Смены -----------------------------------------------------------------


def test_shifts_split_on_the_long_break():
    morning = [rec(created=datetime(2026, 9, 10, 7, 0) + timedelta(minutes=i)) for i in range(3)]
    evening = [rec(created=datetime(2026, 9, 10, 17, 0) + timedelta(minutes=i)) for i in range(2)]
    assert [len(s) for s in shifts(morning + evening)] == [3, 2]


def test_single_shift_is_not_split_by_a_quiet_stretch():
    """Полчаса без машин внутри смены — это затишье, а не конец съёмки."""
    records = [rec(created=datetime(2026, 9, 10, 7, 0)),
               rec(created=datetime(2026, 9, 10, 7, 35))]
    assert len(shifts(records)) == 1


# ---- Настоящая выгрузка ----------------------------------------------------
#
# Файл с бортовыми номерами и перевозчиками в репозиторий не кладётся
# (решения 016 и 018), поэтому в CI этот блок пропускается, а локально служит
# главной проверкой: синтетика не воспроизводит порчу, которой мы не ожидали.

from pathlib import Path  # noqa: E402

from paxcount.delivery.operator import for_stop, read_export  # noqa: E402

EXPORT = (Path(__file__).resolve().parents[1] / "data/prod_videos/видео 1"
          / "2026-09-14_Fixatsia_transporta_na_ostanovkakh.xlsx")
STOP = "22739"


@pytest.mark.skipif(not EXPORT.exists(),
                    reason="боевая выгрузка не версионируется (персональные данные)")
class TestRealExport:
    @pytest.fixture(scope="class")
    @classmethod
    def records(cls):
        return for_stop(read_export(EXPORT), STOP)

    def test_stop_slice_matches_the_measurement(self, records):
        """23 остановки в файле, наша — 292 записи."""
        assert len(records) == 292

    def test_three_shifts_with_measured_sizes(self, records):
        assert [len(s) for s in shifts(records)] == [116, 72, 104]

    def test_exactly_five_records_are_malformed(self, records):
        """Пять — это замер, а не круглое число: изменится файл, упадёт тест."""
        assert sum(not r.well_formed for r in records) == 5

    def test_four_of_five_are_repairable_without_the_portal(self, records):
        """Перестановка и склейка читаются однозначно, шесть цифр в борте — нет."""
        broken = [r for r in records if not r.well_formed]
        repaired = [repair(r) for r in broken]
        assert sum(note is not None for _, note in repaired) == 4
        unfixed = [fixed for fixed, note in repaired if note is None]
        assert [r.board for r in unfixed] == ["382252"]

    def test_fifty_one_duplicates_removed(self, records):
        """51, а не 50: пятьдесят первый — борт 38427 через 63 секунды.

        Прежнее окно в минуту обрезало его по краю, и он уходил в книгу второй
        строкой той же машины. Замер: интервалы одного борта — 51 пара до 63 с,
        следующая через два часа.
        """
        result = drop_duplicates(records)
        assert len(result.dropped) == 51
        assert len(result.kept) == 241
        assert 537 in [r.row for r in result.dropped], "борт 38427, 08:36:55"

    def test_four_pairs_disagree_on_the_route(self, records):
        """Оператор сам себя поправил четырежды — эти маршруты под сомнением."""
        assert len(drop_duplicates(records).disputed) == 4

    def test_no_healthy_record_is_repaired(self, records):
        healthy = [r for r in records if r.well_formed]
        assert all(repair(r) == (r, None) for r in healthy)
