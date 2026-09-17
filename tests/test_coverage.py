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
