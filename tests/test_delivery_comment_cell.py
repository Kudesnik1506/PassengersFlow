"""Графа комментария: код таблицы 2 и аномалия съёмки в одной ячейке.

Требование заказчика (17.09): каждую аномалию отражать в комментариях. Дверь
не видно — код; разрыв записи — вдобавок к коду упоминание разрыва с номером
камеры и временем.

Отсюда разделение внутри строки. Код остаётся числом и остаётся проверяемым по
таблице 2: опечатка вроде «9» обязана отлетать в модели, а не уходить в файл.
Аномалии живут отдельным списком строк. Склеивает их слой записи — как и с
`N/A`, где модель хранит `None`, а показывать его решает писатель.

Время разрыва даётся на той же шкале, что и время строки. Смешивать в одной
строке часы разных камер значит заставить проверяющего гадать, попадает ли эта
машина в названный разрыв, — а ровно для этого упоминание и пишется.
"""

from __future__ import annotations

from datetime import datetime

import pytest
from paxcount.delivery.model import DeliveryRow, VehicleKind


def row(**over) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=35,
                stop="22739", kind=VehicleKind.BUS, state_number="А000АА00")
    base.update(over)
    return DeliveryRow(**base)


# ---- Склейка ячейки ---------------------------------------------------------


def test_code_alone_stays_a_bare_number():
    """Обычный случай: так сдаёт и заказчик в принятом файле."""
    assert row(comment=7).comment_cell == "7"


def test_note_alone_is_the_text():
    assert row(notes=("разрыв записи камеры 2",)).comment_cell == "разрыв записи камеры 2"


def test_code_and_note_live_together():
    cell = row(comment=7, notes=("разрыв записи камеры 2 07:33:45-07:51:39",)).comment_cell
    assert cell.startswith("7")
    assert "разрыв" in cell and "камеры 2" in cell


def test_several_notes_are_all_kept():
    cell = row(notes=("первая аномалия", "вторая аномалия")).comment_cell
    assert "первая аномалия" in cell and "вторая аномалия" in cell


def test_nothing_to_say_leaves_the_cell_empty():
    """Пусто, а не «нет замечаний»: в принятом файле графа просто пустая."""
    assert row().comment_cell is None


# ---- Код по-прежнему проверяется --------------------------------------------


def test_code_outside_table_two_is_still_rejected():
    with pytest.raises(ValueError):
        row(comment=9)


def test_notes_do_not_smuggle_a_code_past_the_check():
    """Аномалия — это текст рядом с кодом, а не способ вписать свой код."""
    assert row(notes=("9",)).comment is None


# ---- Текст про разрыв -------------------------------------------------------


def test_gap_note_names_the_camera_and_both_ends():
    from paxcount.delivery.reconcile import gap_note

    note = gap_note("2", datetime(2026, 9, 10, 7, 33, 45),
                     datetime(2026, 9, 10, 7, 51, 39))
    assert "2" in note
    assert "07:33:45" in note and "07:51:39" in note
    assert "разрыв" in note.lower()


def test_gap_note_survives_into_the_row():
    from paxcount.delivery.reconcile import gap_note

    note = gap_note("2", datetime(2026, 9, 10, 7, 33, 45),
                     datetime(2026, 9, 10, 7, 51, 39))
    assert note in row(comment=5, notes=(note,)).comment_cell


# ---- Дорога в файл и обратно ------------------------------------------------


def test_comment_with_an_anomaly_survives_the_round_trip(tmp_path):
    """Код и текст уходят в одну ячейку и возвращаются разделёнными.

    Разделять при чтении обязательно: иначе следующая же выгрузка получит в
    графе кода строку «7; разрыв...», и проверка по таблице 2 отвергнет
    собственный файл проекта.
    """
    from paxcount.delivery.xlsx import read_rows, write_rows

    source = row(comment=7, notes=("разрыв записи камеры 2 07:33:45-07:51:39",))
    path = write_rows(tmp_path / "бланк.xlsx", [source])
    back = read_rows(path)[0]
    assert back.comment == 7
    assert back.notes == ("разрыв записи камеры 2 07:33:45-07:51:39",)


def test_plain_code_still_reads_back_as_a_number(tmp_path):
    """Файлы заказчика без аномалий читаются как раньше."""
    from paxcount.delivery.xlsx import read_rows, write_rows

    path = write_rows(tmp_path / "бланк.xlsx", [row(comment=5)])
    back = read_rows(path)[0]
    assert back.comment == 5 and back.notes == ()


def test_note_without_a_code_reads_back_as_a_note(tmp_path):
    from paxcount.delivery.xlsx import read_rows, write_rows

    path = write_rows(tmp_path / "бланк.xlsx", [row(notes=("машина во втором ряду",))])
    back = read_rows(path)[0]
    assert back.comment is None
    assert back.notes == ("машина во втором ряду",)


# ---- Кто порождает заметки ---------------------------------------------------


def gap(camera: str, h: int, m: int, s: int, length: float):
    from paxcount.delivery.coverage import Gap

    return Gap(camera=camera, start=datetime(2026, 9, 10, h, m, s),
                duration_s=length, operator_records_inside=0)


def test_visit_inside_a_gap_gets_the_note():
    from paxcount.delivery.reconcile import notes_for_visit

    notes = notes_for_visit(datetime(2026, 9, 10, 7, 40, 0),
                             [gap("2", 7, 33, 45, 1074.0)])
    assert len(notes) == 1
    assert "камеры 2" in notes[0] and "07:33:45" in notes[0]


def test_visit_outside_every_gap_gets_nothing():
    from paxcount.delivery.reconcile import notes_for_visit

    assert notes_for_visit(datetime(2026, 9, 10, 7, 5, 0),
                            [gap("2", 7, 33, 45, 1074.0)]) == ()


def test_gaps_of_different_cameras_are_named_separately():
    """Заказчик просил номер камеры: две дыры — две заметки, а не одна общая."""
    from paxcount.delivery.reconcile import notes_for_visit

    notes = notes_for_visit(datetime(2026, 9, 10, 7, 40, 0),
                             [gap("3", 7, 39, 0, 120.0), gap("2", 7, 33, 45, 1074.0)])
    assert len(notes) == 2
    assert "камеры 2" in notes[0] and "камеры 3" in notes[1], "порядок по камере"


def test_visit_running_into_a_gap_is_also_an_anomaly():
    """Машина встала до разрыва и стояла в нём — счёт оборван, это тоже аномалия.

    Порог тут не нужен и не выдумывается: границы даёт сама стоянка.
    """
    from paxcount.delivery.reconcile import notes_for_visit

    notes = notes_for_visit(datetime(2026, 9, 10, 7, 33, 35),
                             [gap("2", 7, 33, 45, 1074.0)], window_s=20.0)
    assert len(notes) == 1


def test_a_visit_that_ends_before_the_gap_is_not_an_anomaly():
    from paxcount.delivery.reconcile import notes_for_visit

    assert notes_for_visit(datetime(2026, 9, 10, 7, 33, 20),
                            [gap("2", 7, 33, 45, 1074.0)], window_s=10.0) == ()
