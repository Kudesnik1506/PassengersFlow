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

from dataclasses import replace
from datetime import datetime

import pytest

from paxcount.delivery.timeline import (
    FileSlot,
    NoFootageError,
    Session,
    file_at,
    parse_slot,
    sessions_from_names,
)

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


# ---- Три камеры на одной остановке -------------------------------------------
#
# Боевая съёмка пришла с трёх камер, и имя файла несёт их номер: `22739_1` —
# остановка 22739, камера 1. Без разбора этого поля весь номер уходил в
# `stop`, и три камеры выглядели тремя разными остановками.
#
# Хуже другое: камеры пишут по-разному. Первая режет ровно по 25 минут, третья
# по 10, вторая — кусками от минуты до четверти часа. И стартуют они вразнобой:
# 06:56:30, 06:55:02, 06:50:46. Пока длительность куска мерялась по следующему
# слоту общего списка, соседом оказывался кусок чужой камеры — с чужим шагом и
# чужим временем старта.

CAM_NAMES = [
    "2026-09-10 - 06-56-30 - 22739_1 - 01",   # камера 1, шаг 25 минут
    "2026-09-10 - 07-21-30 - 22739_1 - 02",
    "2026-09-10 - 06-55-02 - 22739_2 - 01",   # камера 2, кусок 9 минут
    "2026-09-10 - 07-04-02 - 22739_2 - 02",
]


def test_camera_is_read_from_the_name():
    slot = parse_slot("2026-09-10 - 06-56-30 - 22739_1 - 01")
    assert slot.stop == "22739"
    assert slot.camera == "1"


def test_stop_without_camera_still_parses():
    """Принятая смена заказчика названа `1715-15182` — без номера камеры."""
    slot = parse_slot("2026-05-19 - 06-59-54 - 1715-15182 - 01")
    assert slot.stop == "1715-15182"
    assert slot.camera == ""


def test_cameras_do_not_collapse_into_one_session():
    sessions = sessions_from_names(CAM_NAMES)
    assert {s.camera for s in sessions} == {"1", "2"}
    assert len(sessions) == 2


def test_duration_is_measured_within_one_camera():
    """Сосед для измерения ищется у своей камеры, иначе шаг выйдет чужой."""
    by_camera = {s.camera: s for s in sessions_from_names(CAM_NAMES)}
    assert by_camera["1"].slots[0].duration_s == 1500.0
    assert by_camera["2"].slots[0].duration_s == 540.0


def test_same_start_on_two_cameras_gives_two_slots():
    """Одинаковое время у разных камер — два куска, а не один.

    `FileSlot` складывается в множество при отсеивании дублей; без камеры в
    ключе два таких имени слиплись бы в один слот, и вторая камера исчезла бы
    молча.
    """
    names = ["2026-09-10 - 07-00-00 - 22739_1 - 05",
             "2026-09-10 - 07-00-00 - 22739_2 - 05"]
    assert sum(len(s.slots) for s in sessions_from_names(names)) == 2


# ---- Настоящая длительность и разрывы записи --------------------------------
#
# Длительность куска раньше всегда мерялась «до старта соседа» — расстояние по
# именам, а не по факту записи. На боевой К2 это молча прятало разрыв: между
# двумя файлами утренней смены реально лежит 1074 с дыры (07:26:42–07:44:36,
# главный провал в разгар пика), а старая мера читала это как «файл шёл до
# самого следующего», то есть 1074 с несуществующей записи внутри слота. Визит,
# чьё смещение попало бы в эту дыру, получил бы файл и позицию в нём — то есть
# кадр, которого там нет.
#
# `duration_of` — внешняя мера (в бою — ffprobe), а не догадка модуля: часть
# длительности куска эта функция не обязана знать заранее (тестам достаточно
# синтетики), а часть — обязана прийти реальной.

GAP_NAMES = [
    "2026-09-10 - 07-00-00 - 22739_2 - 01",  # реально пишет 900 с из 1500 до следующего
    "2026-09-10 - 07-25-00 - 22739_2 - 02",  # 1500 с без разрыва
    "2026-09-10 - 07-50-00 - 22739_2 - 03",
]
REAL_DURATIONS = {
    "2026-09-10 - 07-00-00 - 22739_2 - 01": 900.0,
    "2026-09-10 - 07-25-00 - 22739_2 - 02": 1500.0,
    "2026-09-10 - 07-50-00 - 22739_2 - 03": 600.0,
}


def test_without_duration_of_behaviour_is_unchanged():
    """Старые вызовы без `duration_of` продолжают мерить длительность по соседу."""
    session = sessions_from_names(GAP_NAMES)[0]
    assert session.slots[0].duration_s == pytest.approx(1500.0)
    assert session.gaps_s() == []


def test_real_duration_shorter_than_neighbour_gap_is_a_gap():
    session = sessions_from_names(GAP_NAMES, duration_of=REAL_DURATIONS.get)[0]
    assert session.slots[0].real_duration_s == pytest.approx(900.0)
    assert session.slots[0].gap_after_s == pytest.approx(600.0)


def test_slot_with_no_gap_reports_zero():
    session = sessions_from_names(GAP_NAMES, duration_of=REAL_DURATIONS.get)[0]
    assert session.slots[1].gap_after_s == pytest.approx(0.0)


def test_session_lists_gaps_on_its_own_offset_scale():
    """Дыра начинается на 900-й секунде смены (конец реальной записи) и длится 600 с."""
    session = sessions_from_names(GAP_NAMES, duration_of=REAL_DURATIONS.get)[0]
    assert session.gaps_s() == [(900.0, 600.0)]


def test_locate_inside_a_gap_refuses_to_point_at_a_file():
    """Смещение внутри дыры — не «дальше в этом файле», а отсутствие записи."""
    session = sessions_from_names(GAP_NAMES, duration_of=REAL_DURATIONS.get)[0]
    with pytest.raises(NoFootageError):
        session.locate(1200.0)  # 900..1500 — дыра


def test_locate_right_after_the_gap_reaches_the_next_file():
    session = sessions_from_names(GAP_NAMES, duration_of=REAL_DURATIONS.get)[0]
    slot, offset = session.locate(1500.0)
    assert slot.index == 2 and offset == pytest.approx(0.0)


def test_at_edge_covers_gap_boundaries_too():
    """N/A встаёт не только на краях смены, но и на краях каждой дыры (план, шаг 0б)."""
    session = sessions_from_names(GAP_NAMES, duration_of=REAL_DURATIONS.get)[0]
    assert session.at_edge(897.0, guard_s=5.0) is True   # перед дырой
    assert session.at_edge(1503.0, guard_s=5.0) is True  # сразу после дыры
    assert session.at_edge(700.0, guard_s=5.0) is False  # обычная запись


def test_no_footage_error_is_a_value_error():
    """Старый код, ловящий ValueError от locate(), продолжает его ловить."""
    assert issubclass(NoFootageError, ValueError)


# ---- Какой файл открывать на этот момент -------------------------------------
#
# Графа N бланка называется «Название видеофайла. Скопировать сюда»: по ней
# проверяющий открывает запись и смотрит машину. Имя файла, внутри которого
# момента нет, хуже пустой графы — оно отправляет смотреть не туда и выглядит
# при этом заполненным.
#
# Поэтому решает измеренная длительность записи, а не расстояние до соседа: у
# боевой К2 между файлами есть дыры (главная — 1074 с), и момент, попавший в
# дыру, не лежит ни в одном файле.

def test_the_file_of_a_moment_is_the_one_that_contains_it():
    slots = [parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01"),
              parse_slot("2026-09-10 - 07-10-00 - 22739_2 - 02")]
    assert file_at(datetime(2026, 9, 10, 7, 5), slots) == \
        "2026-09-10 - 07-00-00 - 22739_2 - 01"
    assert file_at(datetime(2026, 9, 10, 7, 12), slots) == \
        "2026-09-10 - 07-10-00 - 22739_2 - 02"


def test_a_moment_inside_a_recording_gap_has_no_file():
    """Файл кончился раньше соседа — момент между ними не снят вовсе."""
    short = replace(parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01"),
                     real_duration_s=120.0)
    slots = [short, parse_slot("2026-09-10 - 07-10-00 - 22739_2 - 02")]
    assert file_at(datetime(2026, 9, 10, 7, 5), slots) == "", "две минуты записи, дыра дальше"


def test_a_moment_before_the_first_file_has_no_file():
    slots = [parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01")]
    assert file_at(datetime(2026, 9, 10, 6, 50), slots) == ""


# ---- Запись ищется по всем камерам, а не по одной ----------------------------
#
# В графу N до сих пор шёл файл только К2. Но К2 теряет 1074 с в главный провал,
# а К1 в это же время писала без единого разрыва (замер `tools/coverage.py`), и
# 14 строк книги остались без файла при том, что запись есть. Проверяющему это
# читается как «не снято», хотя снято другой камерой.
#
# Момент приходит на ОБЩЕЙ шкале (ноль — К2, решение 036), а файлы каждой камеры
# названы по её СОБСТВЕННЫМ часам, поэтому поиск обязан перевести момент в часы
# камеры. Иначе на К3 (отстаёт на 418 с) будет назван сосед через файл.


def track(camera: str, offset_to_k2_s: float, names: list[str], real: float | None = None):
    from paxcount.delivery.timeline import CameraTrack

    slots = [parse_slot(n) for n in names]
    if real is not None:
        slots = [replace(s, real_duration_s=real) for s in slots]
    return CameraTrack(camera=camera, offset_to_k2_s=offset_to_k2_s, slots=slots)


def test_the_reference_camera_answers_when_it_was_recording():
    from paxcount.delivery.timeline import footage_at

    k2 = track("2", 0.0, ["2026-09-10 - 07-00-00 - 22739_2 - 01"])
    got = footage_at(datetime(2026, 9, 10, 7, 5), [k2])
    assert got.camera == "2"
    assert got.file == "2026-09-10 - 07-00-00 - 22739_2 - 01"
    assert got.camera_ts == datetime(2026, 9, 10, 7, 5), "часы К2 и есть шкала"


def test_another_camera_covers_the_gap_of_the_reference_one():
    """К2 молчит, К1 пишет — строка получает файл К1, а не пустоту."""
    from paxcount.delivery.timeline import footage_at

    k2 = track("2", 0.0, ["2026-09-10 - 07-00-00 - 22739_2 - 01"], real=120.0)
    k1 = track("1", -358.0, ["2026-09-10 - 06-54-02 - 22739_1 - 01"])
    got = footage_at(datetime(2026, 9, 10, 7, 5), [k2, k1])
    assert got.camera == "1"
    assert got.file == "2026-09-10 - 06-54-02 - 22739_1 - 01"


def test_the_moment_is_converted_to_the_clock_of_that_camera():
    """Часы К1 отстают на 358 с: 07:05 общей шкалы — это 06:59:02 на К1.

    Без перевода поиск назвал бы файл по чужим часам, а проверяющий перемотал
    бы запись на шесть минут мимо машины.
    """
    from paxcount.delivery.timeline import footage_at

    k2 = track("2", 0.0, ["2026-09-10 - 07-00-00 - 22739_2 - 01"], real=120.0)
    k1 = track("1", -358.0, ["2026-09-10 - 06-54-02 - 22739_1 - 01"])
    assert footage_at(datetime(2026, 9, 10, 7, 5), [k2, k1]).camera_ts == \
        datetime(2026, 9, 10, 6, 59, 2)


def test_a_moment_no_camera_recorded_has_no_footage():
    """Молчали все — графа остаётся пустой, а не заполняется ближайшим файлом."""
    from paxcount.delivery.timeline import footage_at

    k2 = track("2", 0.0, ["2026-09-10 - 07-00-00 - 22739_2 - 01"], real=120.0)
    k1 = track("1", -358.0, ["2026-09-10 - 06-54-02 - 22739_1 - 01"], real=120.0)
    assert footage_at(datetime(2026, 9, 10, 7, 5), [k2, k1]) is None


def test_the_order_of_cameras_is_the_order_of_preference():
    """Пишут обе — берётся первая в списке: у вызывающего своя политика камер."""
    from paxcount.delivery.timeline import footage_at

    k2 = track("2", 0.0, ["2026-09-10 - 07-00-00 - 22739_2 - 01"])
    k1 = track("1", -358.0, ["2026-09-10 - 06-54-02 - 22739_1 - 01"])
    assert footage_at(datetime(2026, 9, 10, 7, 5), [k1, k2]).camera == "1"


# ---- Дыры записи по плоскому списку кусков ------------------------------------
#
# `Session.gaps_s` отвечает про одну смену, а книга собирается на сутки: у К2 за
# день три смены, между ними по два часа, и для строки, попавшей между ними,
# ответ «записи нет» такой же честный, как внутри разрыва самой смены.


def test_a_short_file_before_its_neighbour_makes_a_gap():
    from paxcount.delivery.timeline import gaps_between

    slots = [replace(parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01"), real_duration_s=120.0),
              parse_slot("2026-09-10 - 07-10-00 - 22739_2 - 02")]
    assert gaps_between(slots) == [(datetime(2026, 9, 10, 7, 2), 480.0)]


def test_files_butt_joined_make_no_gap():
    from paxcount.delivery.timeline import gaps_between

    slots = [replace(parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01"), real_duration_s=600.0),
              parse_slot("2026-09-10 - 07-10-00 - 22739_2 - 02")]
    assert gaps_between(slots) == []


def test_an_unmeasured_file_claims_no_gap():
    """Длительность не измерена — про дыру после файла утверждать нечем."""
    from paxcount.delivery.timeline import gaps_between

    slots = [parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01"),
              parse_slot("2026-09-10 - 07-10-00 - 22739_2 - 02")]
    assert gaps_between(slots) == []
