"""Сводная таблица: разметка, эталонная строка, часы камер и результаты методов.

Таблица сводит четыре разных источника, и каждая сшивка здесь может тихо
соврать. Три проверяются ниже, потому что каждая уже давала неверную клетку:

* время на соседней камере — это ПЕРЕСЧЁТ, а не замер, и он возможен только по
  файлу, для которого поправка измерена. Одолжить поправку у соседнего файла
  той же камеры нельзя (`clocks` этого и не даёт), а молча показать пересчёт
  как замер — значит выдать за наблюдение арифметику с разбросом в десять
  секунд;
* эталонная строка сшивается с разметкой по времени ТОЙ камеры, на которой
  разметка сделана: у визита на К3 время К2 может быть не замерено вовсе;
* дверь за краем кадра метод найти не мог, и «не нашёл» про неё — ложь.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from paxcount.bench.cases import Case
from paxcount.bench.summary import MethodRun, camera_times, door_hits, match_row
from paxcount.delivery.clocks import ClockRecord
from paxcount.delivery.model import VehicleSize
from paxcount.delivery.timeline import sessions_from_names
from paxcount.truth_rows import TruthRow

K2_FILE = "2026-09-10 - 06-56-06 - 22739_2 - 02"
K3_FILE = "2026-08-10 - 06-50-46 - 22739_3 - 01"
K3_EARLIER = "2026-08-10 - 06-40-46 - 22739_3 - 99"

RECORDS = [
    ClockRecord(camera="2", file=K2_FILE, offset_to_reference_s=0.0, reference_camera="2", measured_by="ноль шкалы"),
    ClockRecord(camera="3", file=K3_FILE, offset_to_reference_s=-418.0, reference_camera="2", measured_by="пара",
                 date_override="2026-09-10"),
]


def slots():
    names = [K2_FILE, K3_EARLIER, K3_FILE, "2026-08-10 - 07-00-46 - 22739_3 - 02"]
    return [s for session in sessions_from_names(names) for s in session.slots]


def times_at(moment: datetime):
    return {t.camera: t for t in camera_times(moment, ("2", "3"), RECORDS, slots(), marked_on="2")}


# ---- Время на соседних камерах ---------------------------------------------


def test_time_on_the_camera_that_was_marked_is_a_measurement():
    times = times_at(datetime(2026, 9, 10, 6, 58, 51))
    assert times["2"].measured, "человек видел машину именно на этом кадре"
    assert times["2"].moment == datetime(2026, 9, 10, 6, 58, 51)


def test_time_on_the_other_camera_is_marked_as_arithmetic():
    times = times_at(datetime(2026, 9, 10, 6, 58, 51))
    assert times["3"].moment == datetime(2026, 8, 10, 6, 51, 53), "дата — как в имени файла К3"
    assert not times["3"].measured, "это пересчёт по поправке, а не наблюдение"


def test_time_is_refused_when_the_measured_file_does_not_cover_it():
    """Визит раньше файла, для которого поправка измерена: клетка пуста.

    Соседний файл той же камеры поправку не одалживает — у К3 три независимые
    пары дали 418, 419 и 428 с, и разница между ними больше, чем расстояние
    между соседними машинами.
    """
    times = times_at(datetime(2026, 9, 10, 6, 56, 59))
    assert times["3"].moment is None
    assert times["3"].problem and "поправк" in times["3"].problem


# ---- Сшивка эталонной строки с разметкой ------------------------------------


def row(**over) -> TruthRow:
    base = dict(number="1", k1_arrival=None, k2_arrival=None, k3_arrival=None,
                vehicle_kind="Автобус", route="26", board_number="7861",
                state_number=None, stood_where=None, size=VehicleSize.LARGE,
                doors_total=3, boarded=None, alighted=None, comment=None)
    base.update(over)
    return TruthRow(**base)


def test_row_joins_by_the_time_of_the_camera_the_markup_was_made_on():
    """Визит размечен на К3 — сшивается по k3_arrival, а не по пустому k2."""
    found = match_row(camera="3", moment=datetime(2026, 8, 10, 6, 58, 6),
                       rows=[row(k3_arrival=datetime(2026, 8, 10, 6, 58, 1))])
    assert found is not None and found.board_number == "7861"


def test_row_further_than_a_stop_is_not_joined():
    """Разрыв больше стоянки — это другая машина, а не та же с другим временем."""
    assert match_row(camera="3", moment=datetime(2026, 8, 10, 6, 58, 6),
                      rows=[row(k3_arrival=datetime(2026, 8, 10, 6, 57, 0))]) is None


def test_row_without_a_time_on_that_camera_is_not_joined():
    assert match_row(camera="3", moment=datetime(2026, 8, 10, 6, 58, 6),
                      rows=[row(k2_arrival=datetime(2026, 9, 10, 7, 5, 4))]) is None


# ---- Попадания по дверям ----------------------------------------------------


def case_of(doors) -> Case:
    return Case(visit_key="2/x/—", camera="2", video=K2_FILE, frame_idx=1,
                 frame_size=(1920, 1080), body_px=(0.0, 0.0, 1000.0, 900.0),
                 size=VehicleSize.LARGE, orientation="нос-справа",
                 doors_visible=tuple(doors), doors_total=3)


def test_door_hits_name_the_methods_that_found_that_very_door():
    case = case_of([(100.0, 400.0, 160.0, 800.0), (600.0, 400.0, 660.0, 800.0)])
    runs = [
        MethodRun(method="edges", visit_key=case.visit_key, status="ок", note="",
                   predicted=((595.0, 390.0, 665.0, 810.0),)),
        MethodRun(method="pixels", visit_key=case.visit_key, status="ок", note="",
                   predicted=()),
    ]
    hits = door_hits(case, runs)
    assert hits[0] == (), "первую дверь не нашёл никто"
    assert hits[1] == ("edges",)


def test_method_that_did_not_run_is_not_counted_as_a_miss():
    """«Не установлен» — не ноль найденных: это разные исходы (bench/run.py)."""
    case = case_of([(100.0, 400.0, 160.0, 800.0)])
    runs = [MethodRun(method="vlm", visit_key=case.visit_key, status="не установлен",
                       note="нет ответов", predicted=())]
    assert door_hits(case, runs) == ((),)
    assert door_hits(case, runs, methods=("vlm",)) == ((),)


def test_door_matches_carry_how_far_the_prediction_missed_the_centre():
    """Попадание попаданию рознь: проём 5 px и запас кропа 140 px значат, что
    «накрыл» может стоять при промахе в полкузова. Смещение центра — то, чем
    эти два попадания отличаются, и в таблице оно обязано быть видно."""
    from paxcount.bench.summary import door_matches

    case = case_of([(600.0, 400.0, 660.0, 800.0)])
    runs = [MethodRun(method="edges", visit_key=case.visit_key, status="ок", note="",
                       predicted=((680.0, 390.0, 740.0, 810.0),))]
    match = door_matches(case, runs)[0]["edges"]
    assert match.hit
    assert round(match.center_error_px) == 80
