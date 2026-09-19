"""Карта покрытия: сколько записи реально не хватает и чем это грозит.

Разрыв в записи — не абстрактная дыра, а конкретные машины, которые оператор
отметил, а камера не сняла. Главный провал К2 боевой смены (07:26:42–07:44:36)
приходится на разгар пика — 10 записей оператора внутри него. Отчёт держит эти
две вещи рядом (длина дыры и что в неё попало у оператора), потому что по
одной длине нельзя судить о цене: 1074 с в пик и 1074 с в затишье — разная
потеря.

Мелкие стыки между файлами (2-4 с, обычное дело у боевых камер) в отчёт не
идут — `min_gap_s` их отсекает, чтобы значимая дыра не терялась в тридцати
строках технического шума.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from paxcount.delivery.coverage import Gap, gaps_for, render_report
from paxcount.delivery.timeline import sessions_from_names

NAMES = [
    "2026-09-10 - 07-00-00 - 22739_2 - 01",
    "2026-09-10 - 07-25-00 - 22739_2 - 02",
]
REAL_DURATIONS = {NAMES[0]: 900.0, NAMES[1]: 1500.0}  # разрыв 600 с


def session():
    return sessions_from_names(NAMES, duration_of=REAL_DURATIONS.get)[0]


def test_gap_is_reported_with_wall_clock_bounds():
    gaps = gaps_for("2", session(), min_gap_s=5.0, operator_count=lambda a, b: 0)
    assert gaps == [Gap(camera="2", start=datetime(2026, 9, 10, 7, 15, 0),
                        duration_s=600.0, operator_records_inside=0)]


def test_operator_count_receives_the_gap_bounds():
    seen = []

    def counter(start, end):
        seen.append((start, end))
        return 3

    gaps = gaps_for("2", session(), min_gap_s=5.0, operator_count=counter)
    assert gaps[0].operator_records_inside == 3
    assert seen == [(datetime(2026, 9, 10, 7, 15, 0), datetime(2026, 9, 10, 7, 25, 0))]


def test_gaps_shorter_than_threshold_are_not_stitches_worth_reporting():
    """Стыки в 2-4 с — обычное дело у боевых камер, не разрыв, о котором надо знать."""
    short = sessions_from_names(
        ["2026-09-10 - 07-00-00 - 22739_3 - 01", "2026-09-10 - 07-10-03 - 22739_3 - 02"],
        duration_of={"2026-09-10 - 07-00-00 - 22739_3 - 01": 600.0}.get,
    )[0]
    assert gaps_for("3", short, min_gap_s=5.0, operator_count=lambda a, b: 0) == []


def test_render_report_names_the_camera_and_the_price_of_the_gap():
    gaps = [Gap(camera="2", start=datetime(2026, 9, 10, 7, 26, 42),
                duration_s=1074.0, operator_records_inside=10)]
    text = render_report(gaps)
    assert "2" in text and "1074" in text and "10" in text


def test_render_report_of_no_gaps_says_so():
    assert "нет" in render_report([]).lower()


def test_gap_needs_real_durations_to_exist():
    """Без duration_of сессия не знает о дырах — это тоже надо честно показать."""
    plain = sessions_from_names(NAMES)[0]
    assert gaps_for("2", plain, min_gap_s=5.0, operator_count=lambda a, b: 0) == []


# ---- Графы записи в строке книги: файл, камера, причина ------------------------
#
# Заказчик просил отражать аномалию съёмки в комментарии, называя камеру и
# время. До сих пор строка, попавшая в разрыв К2, просто оставалась без файла —
# и это читалось как «не снято», хотя К1 в главный провал (1074 с) писала без
# перерыва. Теперь файл находится по любой камере, а разрыв всё равно называется:
# он объясняет, почему у соседних строк файлы разных камер.

from datetime import datetime as _dt

from paxcount.delivery.coverage import with_footage
from paxcount.delivery.model import DeliveryRow, VehicleKind
from paxcount.delivery.timeline import CameraTrack, parse_slot


def _row() -> DeliveryRow:
    return DeliveryRow(group="697", date="10.09.2026", hours=7, minutes=35,
                        stop="22739", kind=VehicleKind.BUS)


def _tracks(k2_real: float | None = None) -> list[CameraTrack]:
    from dataclasses import replace as _replace

    k2 = parse_slot("2026-09-10 - 07-30-00 - 22739_2 - 01")
    if k2_real is not None:
        k2 = _replace(k2, real_duration_s=k2_real)
    return [
        CameraTrack(camera="2", offset_to_reference_s=0.0,
                     slots=[k2, parse_slot("2026-09-10 - 07-50-00 - 22739_2 - 02")]),
        CameraTrack(camera="1", offset_to_reference_s=-358.0,
                     slots=[parse_slot("2026-09-10 - 07-24-02 - 22739_1 - 01")]),
    ]


def test_a_row_the_reference_camera_filmed_names_that_camera():
    out = with_footage(_row(), _dt(2026, 9, 10, 7, 35), _tracks(), [])
    assert out.video == "2026-09-10 - 07-30-00 - 22739_2 - 01"
    assert out.camera_cell == "К2 07:35:00"
    assert out.notes == (), "разрывов нет — объяснять нечего"


def test_a_row_inside_the_reference_gap_is_filmed_by_another_camera():
    """К2 писала две минуты, К1 писала всё время: строка не остаётся пустой."""
    tracks = _tracks(k2_real=120.0)
    gaps = [Gap(camera="2", start=_dt(2026, 9, 10, 7, 32), duration_s=1080.0,
                 operator_records_inside=12)]
    out = with_footage(_row(), _dt(2026, 9, 10, 7, 35), tracks, gaps)
    assert out.camera_cell == "К1 07:29:02", "часы К1 отстают на 358 с"
    assert out.video == "2026-09-10 - 07-24-02 - 22739_1 - 01"


def test_the_gap_is_named_even_when_another_camera_covered_it():
    """Запись нашлась, но разрыв К2 — факт, и он объясняет чужой файл в строке."""
    tracks = _tracks(k2_real=120.0)
    gaps = [Gap(camera="2", start=_dt(2026, 9, 10, 7, 32), duration_s=1080.0,
                 operator_records_inside=12)]
    out = with_footage(_row(), _dt(2026, 9, 10, 7, 35), tracks, gaps)
    assert out.notes and "разрыв записи камеры 2" in out.notes[0]
    assert "07:32:00-07:50:00" in out.notes[0]


def test_a_row_nobody_filmed_keeps_an_empty_file_and_says_why():
    tracks = [_tracks(k2_real=120.0)[0]]
    gaps = [Gap(camera="2", start=_dt(2026, 9, 10, 7, 32), duration_s=1080.0,
                 operator_records_inside=12)]
    out = with_footage(_row(), _dt(2026, 9, 10, 7, 35), tracks, gaps)
    assert out.video == "" and out.camera_cell == ""
    assert out.notes and "разрыв записи камеры 2" in out.notes[0]


# ---- Дыры всех камер на общей шкале -------------------------------------------
#
# Строка книги стоит на шкале К2, а дыры каждая камера прожила в своих часах.
# Сводить их надо в одних часах, иначе «попадает или нет» не прочесть. И порог
# тот же, что в отчёте о покрытии: стык в 2-4 с — обычное дело у боевых камер,
# и превращать его в строку комментария значит топить настоящий провал в шуме.


def test_gaps_of_all_cameras_come_back_on_the_common_scale():
    from dataclasses import replace as _replace

    from paxcount.delivery.coverage import gaps_of_cameras

    k3 = _replace(parse_slot("2026-08-10 - 07-00-00 - 22739_3 - 01"), real_duration_s=60.0)
    tracks = [CameraTrack(camera="3", offset_to_reference_s=-418.0,
                           slots=[k3, parse_slot("2026-08-10 - 07-10-00 - 22739_3 - 02")])]
    gaps = gaps_of_cameras(tracks)
    assert len(gaps) == 1 and gaps[0].camera == "3"
    assert gaps[0].start == _dt(2026, 8, 10, 7, 7, 58), "07:01:00 на часах К3 плюс 418 с"
    assert gaps[0].duration_s == 540.0


def test_a_technical_joint_is_not_a_gap_worth_naming():
    from dataclasses import replace as _replace

    from paxcount.delivery.coverage import gaps_of_cameras

    short = _replace(parse_slot("2026-09-10 - 07-00-00 - 22739_2 - 01"),
                      real_duration_s=596.0)
    tracks = [CameraTrack(camera="2", offset_to_reference_s=0.0,
                           slots=[short, parse_slot("2026-09-10 - 07-10-00 - 22739_2 - 02")])]
    assert gaps_of_cameras(tracks) == [], "стык в 4 с — шум, а не разрыв"


# ---- Камера, которая стоянку не видит ------------------------------------------
#
# К1 стоит в ста метрах вниз по ходу: борт опознаёт, порядок проездов задаёт, а
# стоянку от проезда не отличает (решение 030). Назвать её файл и промолчать —
# значит отправить проверяющего искать посадку там, где её в кадре нет.
#
# Кода таблицы 2 тут не будет: она описывает, какие двери НЕ попали в кадр, а
# случая «не попала стоянка целиком» в ней нет. Придумывать код нельзя — по той
# же причине, по которой `reconcile.comment_code` отказывается его подбирать.


def test_a_camera_that_cannot_see_the_stop_says_so_in_the_comment():
    from dataclasses import replace as _replace

    k2 = _replace(parse_slot("2026-09-10 - 07-30-00 - 22739_2 - 01"), real_duration_s=120.0)
    tracks = [
        CameraTrack(camera="2", offset_to_reference_s=0.0,
                     slots=[k2, parse_slot("2026-09-10 - 07-50-00 - 22739_2 - 02")]),
        CameraTrack(camera="1", offset_to_reference_s=-358.0,
                     slots=[parse_slot("2026-09-10 - 07-24-02 - 22739_1 - 01")]),
    ]
    out = with_footage(_row(), _dt(2026, 9, 10, 7, 35), tracks, [])
    assert out.camera == "1", "запись нашлась только у неё"
    joined = "; ".join(out.notes)
    assert "камер" in joined and "стоянк" in joined, joined
    assert out.comment is None, "кода таблицы 2 для этого случая нет"


def test_a_counting_camera_needs_no_such_warning():
    tracks = [CameraTrack(camera="2", offset_to_reference_s=0.0,
                           slots=[parse_slot("2026-09-10 - 07-30-00 - 22739_2 - 01")])]
    assert with_footage(_row(), _dt(2026, 9, 10, 7, 35), tracks, []).notes == ()


def test_the_row_keeps_what_it_already_said_about_itself():
    """Пометка строки переживает проставление записи.

    Строка цепочки приходит в сборку со своими словами: «оператор не записал»,
    «стоянка не подтверждена». Файл и часы камеры ей проставляются позже, и
    затереть этими словами прежние значит выдать строку, которая молчит о
    самом спорном в себе.
    """
    row = _row().model_copy(update={"notes": ("оператор машину не записал",)})
    out = with_footage(row, _dt(2026, 9, 10, 7, 35), _tracks(), [])
    assert "оператор машину не записал" in out.notes
