"""Шкала времени смены: имя файла — источник времени камеры.

Инструкция называет время камеры основным источником, а выгрузку оператора —
запасным («если оно неверное или не отображается»). Имя файла даёт это время
без распознавания часов в кадре, и оно же служит ключом сопоставления с
выгрузкой.

Главное, что проверяется здесь: файлы внутри смены непрерывны (06-59-54, затем
ровно 07-09-54), поэтому смена собирается в одну сплошную шкалу, а разрез между
файлами перестаёт быть особым случаем. Разрыв же между сменами (утро → день)
обязан распознаваться, иначе визит «склеится» через трёхчасовую дыру.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from paxcount.delivery.timeline import FileSlot, Session, parse_slot, sessions_from_names

NAMES_MORNING = [
    "2026-05-19 - 06-59-54 - 1715-15182 - 01",
    "2026-05-19 - 07-09-54 - 1715-15182 - 02",
    "2026-05-19 - 07-19-54 - 1715-15182 - 03",
]
NAMES_EVENING = [
    "2026-05-19 - 19-37-27 - 1715-15182 - 53",
    "2026-05-19 - 19-47-27 - 1715-15182 - 54",
]


def test_parse_slot_reads_all_four_parts():
    slot = parse_slot(NAMES_MORNING[0])
    assert slot.start == datetime(2026, 5, 19, 6, 59, 54)
    assert slot.stop == "1715-15182"
    assert slot.index == 1
    assert slot.name == NAMES_MORNING[0]


def test_parse_slot_accepts_video_extension():
    """В колонку N имя пишется без расширения, на диске оно с расширением."""
    slot = parse_slot("2026-05-19 - 06-59-54 - 1715-15182 - 01.mp4")
    assert slot.start == datetime(2026, 5, 19, 6, 59, 54)
    assert slot.name == "2026-05-19 - 06-59-54 - 1715-15182 - 01", "расширение в имя не входит"


@pytest.mark.parametrize("bad", ["мусор", "2026-05-19", "2026-13-45 - 06-59-54 - x - 01"])
def test_parse_slot_rejects_foreign_name(bad):
    """Чужое имя — это ошибка соглашения, а не «файл с нулевым временем»."""
    with pytest.raises(ValueError):
        parse_slot(bad)


def test_absolute_time_adds_offset_inside_file():
    slot = parse_slot(NAMES_MORNING[0])
    assert slot.at(6.0) == datetime(2026, 5, 19, 7, 0, 0)


def test_hours_minutes_drops_seconds():
    """Колонки «часы» и «минуты» — момент прибытия, секунды отбрасываются.

    Округление вверх сдвинуло бы часть прибытий в следующую минуту, а в
    принятой таблице заказчика минуты согласуются с отбрасыванием.
    """
    slot = parse_slot(NAMES_MORNING[0])
    assert slot.hours_minutes(6.0) == (7, 0)
    assert slot.hours_minutes(65.9) == (7, 0), "59.9 секунды — всё ещё нулевая минута"
    assert slot.hours_minutes(66.0) == (7, 1)


def test_contiguous_files_form_one_session():
    sessions = sessions_from_names(NAMES_MORNING)
    assert len(sessions) == 1
    assert sessions[0].duration_s == pytest.approx(3 * 600, abs=1)


def test_gap_between_sessions_splits_timeline():
    """Утро и вечер — разные смены: между ними часы, а не разрез файла."""
    sessions = sessions_from_names(NAMES_MORNING + NAMES_EVENING)
    assert len(sessions) == 2
    assert [len(s.slots) for s in sessions] == [3, 2]


def test_session_offset_is_continuous_across_file_cut():
    """Смена — одна шкала: 700 с от начала лежат во втором файле, на 100-й с."""
    session = sessions_from_names(NAMES_MORNING)[0]
    slot, offset = session.locate(700.0)
    assert slot.index == 2
    assert offset == pytest.approx(100.0, abs=0.01)
    assert session.at(700.0) == datetime(2026, 5, 19, 7, 11, 34)


def test_visit_on_file_cut_is_not_a_special_case():
    """Визит через разрез получает время начала, а не два обрывка.

    Ради этого смена и собирается в сплошную шкалу: 54 разреза за день не
    должны порождать 54 особых случая.
    """
    session = sessions_from_names(NAMES_MORNING)[0]
    # визит начался за 5 с до конца первого файла и длится 30 с
    assert session.hours_minutes(595.0) == (7, 9)
    assert session.at(625.0) == datetime(2026, 5, 19, 7, 10, 19)


def test_session_edges_are_marked_for_na():
    """Начало и конец съёмки — те самые места, где инструкция требует N/A."""
    session = sessions_from_names(NAMES_MORNING)[0]
    assert session.at_edge(2.0, guard_s=5.0) is True
    assert session.at_edge(session.duration_s - 2.0, guard_s=5.0) is True
    assert session.at_edge(300.0, guard_s=5.0) is False


def test_slots_out_of_order_are_sorted():
    sessions = sessions_from_names(list(reversed(NAMES_MORNING)))
    assert [s.index for s in sessions[0].slots] == [1, 2, 3]


def test_session_rejects_offset_outside_range():
    session = sessions_from_names(NAMES_MORNING)[0]
    with pytest.raises(ValueError):
        session.locate(-1.0)
    with pytest.raises(ValueError):
        session.locate(session.duration_s + 1.0)


def test_file_duration_is_measured_not_assumed():
    """Длительность берётся из соседнего файла, а не зашита числом 600.

    Если оператор сменит нарезку на 5 или 15 минут, шкала обязана поехать за
    ней, а не остаться при прежней константе.
    """
    names = [
        "2026-05-19 - 08-00-00 - 1715-15182 - 01",
        "2026-05-19 - 08-05-00 - 1715-15182 - 02",
        "2026-05-19 - 08-10-00 - 1715-15182 - 03",
    ]
    session = sessions_from_names(names)[0]
    assert session.slots[0].duration_s == pytest.approx(300.0)
    assert session.at(310.0) == datetime(2026, 5, 19, 8, 5, 10)


def test_last_slot_duration_falls_back_to_previous():
    """У последнего файла нет соседа справа — длительность берём у предыдущего."""
    session = sessions_from_names(NAMES_MORNING)[0]
    assert session.slots[-1].duration_s == pytest.approx(600.0)


def test_single_file_session_uses_declared_default():
    """Один файл: соседей нет вовсе, берётся объявленная длительность по умолчанию."""
    session = sessions_from_names([NAMES_MORNING[0]])[0]
    assert session.slots[0].duration_s > 0
    assert len(session.slots) == 1


def test_file_slot_is_hashable_and_ordered():
    """Слоты кладутся в множества и сортируются — это опора сборки смен."""
    slots = {parse_slot(n) for n in NAMES_MORNING}
    assert len(slots) == 3
    assert isinstance(sorted(slots)[0], FileSlot)


def test_session_knows_its_date_and_stop():
    session: Session = sessions_from_names(NAMES_MORNING)[0]
    assert session.date.isoformat() == "2026-05-19"
    assert session.stop == "1715-15182"
