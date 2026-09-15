"""Файл для заказчика: заголовки, листы, туда-обратно.

Лист данных ищется **по имени** «Бланк», а не по номеру: в шаблоне он второй, а
в принятой заказчиком таблице третий. Опереться на порядок значит однажды
прочитать словарь вместо данных и не заметить этого.

Заголовки воспроизводятся дословно, включая опечатку «Наполенность» в шапке
заказчика. Исправлять её нельзя: если на той стороне файл разбирают по тексту
заголовка, «исправление» превратится в неизвестную колонку.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from paxcount.delivery.model import DeliveryRow, Occupancy, VehicleKind, VehicleSize
from paxcount.delivery.xlsx import BLANK_SHEET, HEADERS, read_rows, write_rows

REAL_TABLE = Path("docs/документы от Заказчика/2026-05-19-1317-1715-15182.xlsx")


def sample(**over) -> DeliveryRow:
    base = dict(
        group="1317", date="2026-05-19", hours=7, minutes=0, stop="1715-15182",
        kind=VehicleKind.BUS, board_number="38099", state_number="Р512ОЕ198",
        route="481", occupancy=Occupancy.A, size=VehicleSize.LARGE,
        alighted=3, boarded=2,
        video="2026-05-19 - 06-59-54 - 1715-15182 - 01", operator="Иванов Иван",
    )
    base.update(over)
    return DeliveryRow(**base)


def test_headers_match_customer_template_verbatim():
    assert HEADERS[0] == "Группа ОП"
    assert HEADERS[8] == "Наполенность по прибытию", "опечатка заказчика сохраняется"
    assert HEADERS[13] == "Название видеофайла. Скопировать сюда"
    assert len(HEADERS) == 15


def test_round_trip_preserves_every_field(tmp_path):
    rows = [sample(), sample(kind=VehicleKind.TROLLEY, board_number="3144",
                            state_number=None, route="14", minutes=5)]
    path = tmp_path / "out.xlsx"
    write_rows(path, rows)
    back = read_rows(path)
    assert len(back) == 2
    assert back[0].number == "Р512ОЕ198"
    assert back[1].number == "3144"
    assert back[1].kind is VehicleKind.TROLLEY
    assert [r.minutes for r in back] == [0, 5]


def test_na_is_written_as_na_not_zero(tmp_path):
    path = tmp_path / "out.xlsx"
    write_rows(path, [sample(alighted=None, boarded=None)])
    back = read_rows(path)[0]
    assert back.alighted is None and back.boarded is None
    assert back.counted is False


def test_zero_survives_as_zero(tmp_path):
    path = tmp_path / "out.xlsx"
    write_rows(path, [sample(alighted=0, boarded=0)])
    back = read_rows(path)[0]
    assert (back.alighted, back.boarded) == (0, 0)
    assert back.counted is True


def test_workbook_has_three_named_sheets(tmp_path):
    import zipfile

    path = tmp_path / "out.xlsx"
    write_rows(path, [sample()])
    wb = zipfile.ZipFile(path).read("xl/workbook.xml").decode("utf-8")
    for name in ("Служебный лист", BLANK_SHEET, "Комментарии для ЦТП"):
        assert name in wb, f"нет листа {name}"


def test_data_sheet_found_by_name_not_position(tmp_path):
    """Лист данных читается по имени — порядок листов у заказчика плавает."""
    path = tmp_path / "out.xlsx"
    write_rows(path, [sample()], blank_last=True)
    assert read_rows(path)[0].number == "Р512ОЕ198"


def test_missing_number_leaves_cell_empty(tmp_path):
    path = tmp_path / "out.xlsx"
    write_rows(path, [sample(state_number=None)])
    back = read_rows(path)[0]
    assert back.number is None


def test_board_number_is_not_published_to_customer(tmp_path):
    """Бортовой номер автобуса живёт у нас, но в файл заказчика не уходит.

    В графе заказчика для автобуса стоит государственный — таков формат. Наш
    бортовой хранится отдельно (решение 026) и в выгрузку не просачивается.
    """
    import zipfile

    path = tmp_path / "out.xlsx"
    write_rows(path, [sample()])
    blob = zipfile.ZipFile(path).read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert "Р512ОЕ198" in blob
    assert "38099" not in blob


@pytest.mark.skipif(not REAL_TABLE.exists(),
                    reason="принятая таблица заказчика не версионируется (персональные данные)")
def test_reads_real_accepted_table():
    """Наш разбор обязан понимать настоящий принятый файл, а не только свой.

    Файл с реальными госномерами и фамилией в репозиторий не кладётся
    (решение 018), поэтому в CI тест пропускается, а локально — работает.
    """
    rows = read_rows(REAL_TABLE)
    assert len(rows) == 312
    assert {r.kind for r in rows} <= set(VehicleKind)
    counted = [r for r in rows if r.counted]
    assert sum(r.alighted for r in counted) == 1334
    assert sum(r.boarded for r in counted) == 830
