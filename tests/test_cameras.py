"""Направление движения в кадре — свойство камеры, а не визита.

На остановке весь транспорт идёт в одну сторону, поэтому нос кузова не нужно
определять у каждой машины: он следует из направления движения механически.
Спрашивать разметчика про нос на каждом визите — значит тридцать раз просить
ответ, который не меняется, и тридцать раз давать шанс ошибиться.

Настройка на КАМЕРУ, а не на остановку: К2 и К3 стоят на одной опоре спина к
спине и смотрят навстречу друг другу — одно и то же движение выглядит в них
зеркально. Проверено на борте 7861: в один и тот же момент К3 видит его борт
во весь кадр, К2 — только передний край.

Чужую настройку камера не одалживает. Незаданное направление остаётся
незаданным: догадка здесь стоила бы зеркальной нумерации дверей, а с ней —
неверного кода 5-8 в отчёте.
"""

from __future__ import annotations

import pytest
from paxcount.cameras import (
    MOTION_LEFT_TO_RIGHT,
    MOTION_RIGHT_TO_LEFT,
    CameraMotion,
    load,
    motion_for,
    nose_for,
    nose_from_motion,
    save,
)
from paxcount.doorprop.layout import NOSE_LEFT, NOSE_RIGHT


def record(camera="2", motion=MOTION_LEFT_TO_RIGHT) -> CameraMotion:
    return CameraMotion(stop="22739", camera=camera, motion=motion,
                         measured_by="проезд маршрута 26, замерено глазами")


# ---- Нос выводится, а не спрашивается ----------------------------------------


def test_moving_left_to_right_puts_the_nose_on_the_right():
    assert nose_from_motion(MOTION_LEFT_TO_RIGHT) == NOSE_RIGHT


def test_moving_right_to_left_puts_the_nose_on_the_left():
    assert nose_from_motion(MOTION_RIGHT_TO_LEFT) == NOSE_LEFT


def test_unknown_motion_is_refused_not_guessed():
    with pytest.raises(ValueError):
        nose_from_motion("куда-то")


def test_nose_for_a_configured_camera_comes_from_its_motion():
    assert nose_for("22739", "2", [record()]) == NOSE_RIGHT


# ---- Незаданное остаётся незаданным ------------------------------------------


def test_camera_without_a_setting_has_no_nose():
    """Пусто — это вопрос к человеку, а не повод взять «обычное» направление."""
    assert motion_for("22739", "3", [record(camera="2")]) is None
    assert nose_for("22739", "3", [record(camera="2")]) is None


def test_a_camera_never_borrows_a_neighbours_direction():
    """К2 и К3 смотрят навстречу: одолжить направление — получить зеркало."""
    records = [record(camera="2", motion=MOTION_LEFT_TO_RIGHT)]
    assert nose_for("22739", "3", records) is None


def test_another_stop_does_not_answer_for_this_one():
    assert motion_for("11111", "2", [record()]) is None


# ---- Хранение ----------------------------------------------------------------


def test_settings_round_trip(tmp_path):
    path = tmp_path / "22739.csv"
    rows = [record(camera="2"), record(camera="3", motion=MOTION_RIGHT_TO_LEFT)]
    save(path, rows)
    assert load(path) == rows


def test_missing_file_is_an_unstarted_table_not_an_error(tmp_path):
    assert load(tmp_path / "нет.csv") == []


def test_saving_the_same_camera_twice_replaces_it(tmp_path):
    """Переезд камеры или ошибка разметчика правится, а не копится второй строкой."""
    path = tmp_path / "22739.csv"
    save(path, [record(camera="2", motion=MOTION_LEFT_TO_RIGHT)])
    save(path, [record(camera="2", motion=MOTION_RIGHT_TO_LEFT)])
    rows = load(path)
    assert len(rows) == 1 and rows[0].motion == MOTION_RIGHT_TO_LEFT


# ---- Какие камеры вообще размечаются -----------------------------------------


def test_only_cameras_that_see_the_doors_are_marked():
    """К1 стоит в ста метрах вниз по ходу: она опознаёт, но не считает (030).

    Разметка дверей нужна ровно для нарезки кропа под счёт. Размечать камеру,
    по которой счёт не ведётся, — работа, которая никуда не пойдёт.
    """
    from paxcount.cameras import COUNTING_CAMERAS, counts

    assert counts("2") and counts("3")
    assert not counts("1")
    assert "1" not in COUNTING_CAMERAS


def test_a_camera_without_a_number_is_not_assumed_to_count():
    """Съёмка без номера камеры в имени (принятая смена заказчика) — не наша."""
    from paxcount.cameras import counts

    assert not counts("")
