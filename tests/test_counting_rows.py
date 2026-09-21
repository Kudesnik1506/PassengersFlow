"""Счёт строки книги: на какой камере и в какой момент смотреть посадку.

Строка книги знает камеру и её часы (графа P), но не всякая камера видит
двери. К1 стоит в ста метрах выше по ходу и снимает проезд (решение 030), а
строки на ней появились там, где К2 не писала. Посадку такой машины видно
только на К3 — и искать её надо по часам К3, а не по часам К1.
"""

from __future__ import annotations

from datetime import date, datetime

from paxcount.counting.rows import on_camera, on_counting_camera

OFFSETS = {"1": 60.0, "2": 418.0, "3": 0.0}
DAYS = {"1": date(2026, 9, 10), "2": date(2026, 9, 10), "3": date(2026, 8, 10)}


def test_a_counting_camera_is_kept():
    assert on_counting_camera("К2 08:20:20", OFFSETS, DAYS) == (
        "2", datetime(2026, 9, 10, 8, 20, 20))


def test_a_row_on_camera_one_is_counted_on_camera_three():
    """К1 дверей не видит — посадку ищем на К3, по её часам и её дате.

    У К3 в именах файлов август вместо сентября: момент обязан нести дату её
    файлов, иначе запись не найдётся вовсе.
    """
    assert on_counting_camera("К1 08:31:48", OFFSETS, DAYS) == (
        "3", datetime(2026, 8, 10, 8, 30, 48))


def test_a_moment_moves_between_cameras_through_the_common_scale():
    """К2 в провале: тот же момент на К3 — минус её отставание в 418 с."""
    assert on_camera("2", datetime(2026, 9, 10, 8, 20, 20), "3", OFFSETS, DAYS) == (
        datetime(2026, 8, 10, 8, 13, 22))


def test_a_row_without_a_camera_has_nothing_to_count():
    assert on_counting_camera("", OFFSETS, DAYS) is None
