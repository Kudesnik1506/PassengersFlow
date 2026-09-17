"""Часы камер: поправка к К2 — на файл, а не глобальная.

Ноль шкалы — К2, и выбор не произвольный: К2 — единственная камера, на которой
виден самый ранний по времени визит эталонной таблицы (маршрут 26, борт 7326).
У К1 и К3 часы идут иначе, и величина поправки не одна на камеру: К3 давала
−419 / −418 / −428 с по трём независимым парам событий за одно утро —
расхождение крупнее секунды, то есть даже «одна поправка на камеру» уже
подгонка. Отсюда два разных объекта модуля:

* `Measurement` — сырая пара одновременных событий (замер), из которой берётся
  разброс. По разбросу видно, можно ли вообще считать поправку константой.
* `ClockRecord` — уже принятая поправка для конкретного файла, то, чем
  пользуется остальной код при переводе времени между камерами.

Округлять на «примерно одна и та же поправка на камеру» здесь нельзя: часть
кода (`from_reference`, порядок строк в отчёте) использует поправку, чтобы
УПОРЯДОЧИТЬ визиты соседних камер, и секунда ошибки на плотной остановке
(46 % машин делят минуту с соседней) меняет местами строки — а порядок и есть
главный критерий приёмки.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest
from paxcount.delivery.clocks import (
    READY_RESIDUAL_S,
    ClockRecord,
    Measurement,
    from_reference,
    is_ready,
    load,
    save,
    spread_s,
    to_reference,
)

FILE = "2026-08-10 - 06-50-46 - 22739_3 - 01"


def records(**over) -> ClockRecord:
    base = dict(camera="3", file=FILE, offset_to_k2_s=-423.0,
                measured_by="кузов пересекает границу кадров у опоры", date_override=None)
    base.update(over)
    return ClockRecord(**base)


# ---- Применение поправки ----------------------------------------------------


def test_to_reference_subtracts_the_camera_lag():
    """К3 06:57:57 при поправке −423 с → К2 07:05:00 (та же пара, что и раньше)."""
    moment = datetime(2026, 9, 10, 6, 57, 57)
    result = to_reference(moment, "3", FILE, [records(offset_to_k2_s=-423.0)])
    assert result == datetime(2026, 9, 10, 7, 5, 0)


def test_from_reference_is_the_inverse():
    on_k2 = datetime(2026, 9, 10, 7, 5, 0)
    result = from_reference(on_k2, "3", FILE, [records(offset_to_k2_s=-423.0)])
    assert result == datetime(2026, 9, 10, 6, 57, 57)


def test_zero_offset_is_a_legitimate_value():
    """К2 — сама точка отсчёта: у её файлов offset ровно 0, а не «нет записи»."""
    row = records(camera="2", file="2026-09-10 - 06-56-06 - 22739_2 - 02", offset_to_k2_s=0.0)
    moment = datetime(2026, 9, 10, 6, 58, 41)
    assert to_reference(moment, "2", row.file, [row]) == moment


def test_missing_file_refuses_to_guess():
    """Поправка — на файл. Для файла без замера соседний файл её не одалживает."""
    other_file = "2026-08-10 - 07-00-46 - 22739_3 - 02"
    with pytest.raises(LookupError, match=other_file):
        to_reference(datetime(2026, 9, 10, 7, 5, 0), "3", other_file, [records()])


def test_offset_is_looked_up_per_camera_and_file():
    """Тот же час на другой камере не путается с этим файлом."""
    rows = [records(camera="3", offset_to_k2_s=-423.0),
            records(camera="1", file="2026-09-10 - 06-56-30 - 22739_1 - 01", offset_to_k2_s=-376.0)]
    moment = datetime(2026, 9, 10, 6, 56, 30)
    got = to_reference(moment, "1", "2026-09-10 - 06-56-30 - 22739_1 - 01", rows)
    assert got == moment - timedelta(seconds=-376.0)


# ---- Готовность: разброс независимых замеров --------------------------------


def measurement(**over) -> Measurement:
    base = dict(camera="3", file=FILE,
                raw_time=datetime(2026, 9, 10, 6, 57, 57),
                reference_time=datetime(2026, 9, 10, 7, 5, 0))
    base.update(over)
    return Measurement(**base)


def test_measurement_offset_matches_the_worked_example():
    assert measurement().offset_to_k2_s == pytest.approx(-423.0)


def test_spread_of_the_actual_k3_measurements_is_not_ready():
    """Три реальные пары К3↔К2 за одно утро: −419 / −418 / −428 с — разброс 10 с.

    Порог готовности — 2 с (план, шаг 0а). Три секунды разницы между двумя
    парами уже не проходят; называть −423 «поправкой К3» преждевременно.
    """
    base_reference = datetime(2026, 9, 10, 7, 5, 0)
    pairs = [419.0, 418.0, 428.0]
    measurements = [
        measurement(raw_time=base_reference - timedelta(seconds=off), reference_time=base_reference)
        for off in pairs
    ]
    assert spread_s(measurements) == pytest.approx(10.0)
    assert not is_ready(measurements)


def test_a_single_measurement_cannot_be_judged_ready():
    """К1 получен через одно допущение (18-20 с хода), не независимой парой."""
    with pytest.raises(ValueError, match="минимум"):
        spread_s([measurement()])


def test_ready_when_pairs_agree_within_threshold():
    base_reference = datetime(2026, 9, 10, 7, 5, 0)
    measurements = [
        measurement(raw_time=base_reference - timedelta(seconds=100.0), reference_time=base_reference),
        measurement(raw_time=base_reference - timedelta(seconds=101.5), reference_time=base_reference),
    ]
    assert spread_s(measurements) <= READY_RESIDUAL_S
    assert is_ready(measurements)


# ---- Хранение: data/clocks/<остановка>.csv -----------------------------------


def test_round_trips_through_csv(tmp_path):
    path = tmp_path / "22739.csv"
    rows = [records(), records(camera="2", file="x", offset_to_k2_s=0.0),
            records(camera="1", file="y", offset_to_k2_s=-376.0,
                    date_override="2026-09-10")]
    save(path, rows)
    assert load(path) == rows


def test_missing_table_is_an_empty_list_not_an_error(tmp_path):
    """Ещё не начатая таблица — обычное состояние, а не повод падать."""
    assert load(tmp_path / "no-such-file.csv") == []


# ---- Неверная дата в имени файла ---------------------------------------------


def test_date_override_moves_the_moment_to_the_real_day():
    """У К3 в именах файлов август вместо сентября — дата чинится поправкой.

    Замерено на первой боевой разметке: визит борта 7861, снятый К3, лёг на
    2026-08-10 07:05:04, тогда как та же машина на К2 стоит 2026-09-10 07:05:00.
    Время суток сходилось, дата расходилась на месяц — и сшить визит одной
    машины по двум камерам стало нечем. Поправка `date_override` в таблице
    была, но не применялась.
    """
    from datetime import datetime

    from paxcount.delivery.clocks import ClockRecord, to_reference

    name = "2026-08-10 - 06-50-46 - 22739_3 - 01"
    rows = [ClockRecord(camera="3", file=name, offset_to_k2_s=-418.0,
                         measured_by="замер", date_override="2026-09-10")]
    got = to_reference(datetime(2026, 8, 10, 6, 58, 6), "3", name, rows)
    assert got == datetime(2026, 9, 10, 7, 5, 4)


def test_date_override_is_undone_on_the_way_back():
    """Обратный перевод обязан вернуть дату, которая стоит в имени файла."""
    from datetime import datetime

    from paxcount.delivery.clocks import ClockRecord, from_reference

    name = "2026-08-10 - 06-50-46 - 22739_3 - 01"
    rows = [ClockRecord(camera="3", file=name, offset_to_k2_s=-418.0,
                         measured_by="замер", date_override="2026-09-10")]
    got = from_reference(datetime(2026, 9, 10, 7, 5, 4), "3", name, rows)
    assert got == datetime(2026, 8, 10, 6, 58, 6)


def test_without_an_override_the_date_is_left_alone():
    from datetime import datetime

    from paxcount.delivery.clocks import ClockRecord, to_reference

    name = "2026-09-10 - 06-56-30 - 22739_1 - 01"
    rows = [ClockRecord(camera="1", file=name, offset_to_k2_s=-358.0,
                         measured_by="замер")]
    got = to_reference(datetime(2026, 9, 10, 6, 58, 57), "1", name, rows)
    assert got == datetime(2026, 9, 10, 7, 4, 55)
