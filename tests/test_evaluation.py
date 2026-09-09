"""Дефект 3: метрика молча хвалила регресс.

`compare_events` обходила пары «видео + направление», взятые ИЗ ЭТАЛОНА. Поэтому
предсказания того направления, которого в эталоне нет, не проверялись вовсе и не
попадали в FP. Замер, на котором это вскрылось: конфигурация выдумывала на видео
04 четыре входа при эталоне «входов нет» — суммарная погрешность росла с 40 % до
67 %, а precision по событиям оставался 100 %.

Метрика, не видящая собственных ложных срабатываний, опаснее отсутствия метрики:
она даёт основание принять правку, которая всё испортила.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest
from paxcount.evaluate import compare_events
from rich.console import Console

TRUTH_COLUMNS = ["video", "direction", "ts", "count", "observable"]


@pytest.fixture
def console() -> Console:
    """Вывод в буфер: тест не печатает в stdout (принцип 4)."""
    return Console(file=io.StringIO(), width=200)


def write_truth(path, rows) -> None:
    pd.DataFrame(rows, columns=TRUTH_COLUMNS).to_csv(path, index=False)


def write_predictions(run_dir, backend, video, events) -> None:
    """events.csv лежит как <прогон>/<бэкенд>/<видео>/events.csv.

    Имя бэкенда берётся из деда файла — раскладку задаёт `collect_pred_events`,
    поэтому повторяем её здесь буквально.
    """
    out = run_dir / backend / video
    out.mkdir(parents=True)
    pd.DataFrame(
        [{"video": video, "event_ts": ts, "direction": d} for ts, d in events],
        columns=["video", "event_ts", "direction"],
    ).to_csv(out / "events.csv", index=False)


def metrics(run_dir, truth_path, console) -> dict:
    df = compare_events(run_dir, truth_path, console)
    return df[df["набор"] == "все"].iloc[0].to_dict()


def test_invented_events_in_an_unlisted_direction_count_as_false_positives(tmp_path, console):
    """Регресс дефекта: четыре выдуманных входа обязаны попасть в FP."""
    truth = tmp_path / "events.csv"
    write_truth(truth, [
        {"video": "04", "direction": "out", "ts": 10.0, "count": 1, "observable": "yes"},
        {"video": "04", "direction": "out", "ts": 20.0, "count": 1, "observable": "yes"},
    ])
    run_dir = tmp_path / "run"
    write_predictions(run_dir, "custom", "04", [
        (10.1, "out"), (20.1, "out"),
        (11.0, "in"), (12.0, "in"), (13.0, "in"), (14.0, "in"),
    ])

    m = metrics(run_dir, truth, console)
    assert m["TP"] == 2
    assert m["FP"] == 4
    assert m["precision"] < 1.0


def test_videos_without_truth_are_not_punished(tmp_path, console):
    """Обратная сторона правила: без эталона событие не ложное, а неизвестное.

    На ролике без разметки мы не знаем, верное срабатывание или нет. Записывать
    его в FP значило бы штрафовать за неизвестное — и толкать к занижению счёта.
    """
    truth = tmp_path / "events.csv"
    write_truth(truth, [
        {"video": "04", "direction": "out", "ts": 10.0, "count": 1, "observable": "yes"},
    ])
    run_dir = tmp_path / "run"
    write_predictions(run_dir, "custom", "04", [(10.1, "out")])
    write_predictions(run_dir, "custom", "77", [(5.0, "in"), (6.0, "in")])

    m = metrics(run_dir, truth, console)
    assert m["FP"] == 0
    assert m["precision"] == 1.0


def test_missed_truth_event_counts_as_a_miss(tmp_path, console):
    truth = tmp_path / "events.csv"
    write_truth(truth, [
        {"video": "04", "direction": "out", "ts": 10.0, "count": 1, "observable": "yes"},
        {"video": "04", "direction": "out", "ts": 20.0, "count": 1, "observable": "yes"},
    ])
    run_dir = tmp_path / "run"
    write_predictions(run_dir, "custom", "04", [(10.1, "out")])

    m = metrics(run_dir, truth, console)
    assert (m["TP"], m["FP"], m["FN"]) == (1, 0, 1)


def test_match_is_by_time_not_by_count(tmp_path, console):
    """Правильная сумма по неправильным причинам не должна считаться успехом.

    Два события эталона и два предсказания, но в другие моменты времени: это не
    совпадение, а совпадение количеств. Именно эту разницу метрика по событиям
    и существует, чтобы показывать.
    """
    truth = tmp_path / "events.csv"
    write_truth(truth, [
        {"video": "04", "direction": "out", "ts": 10.0, "count": 1, "observable": "yes"},
        {"video": "04", "direction": "out", "ts": 20.0, "count": 1, "observable": "yes"},
    ])
    run_dir = tmp_path / "run"
    write_predictions(run_dir, "custom", "04", [(40.0, "out"), (50.0, "out")])

    m = metrics(run_dir, truth, console)
    assert m["TP"] == 0
    assert m["FP"] == 2
    assert m["FN"] == 2
