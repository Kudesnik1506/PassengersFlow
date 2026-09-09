"""Трение на пути боевой записи — найдено репетицией прогона.

Один из тестовых роликов положили в `data/prod_videos` под именем, каким назвали
бы настоящую запись, и прошли весь путь: прогон без разметки, гейты, эталон,
метрика по домену. Цепочка работает, но всплыли две вещи, каждая из которых
проявилась бы на первой же настоящей записи.
"""

from __future__ import annotations

import io

import pytest
from paxcount.baseline import diff_baseline
from paxcount.doors import markup_state
from rich.console import Console


@pytest.fixture
def console() -> Console:
    return Console(file=io.StringIO(), width=200)


# ---- Гейт baseline не должен падать от самого факта новой записи ----------
#
# Гейт существует, чтобы ловить дрейф чисел на видео, которые уже считались.
# Появление новой записи об этом ничего не говорит: сравнивать не с чем. Но
# `diff_baseline` считала расхождением и её, из-за чего каждая боевая запись
# блокировала бы push до `paxcount baseline --save`. Это приучает запускать
# `--save` не глядя — и гейт перестаёт значить что-либо вообще.


def test_new_video_is_not_a_discrepancy(console):
    old = {"01.webm": {"boarded": 0, "alighted": 0}}
    new = {
        "01.webm": {"boarded": 0, "alighted": 0},
        "2026-09-10_stop.webm": {"boarded": 0, "alighted": 1},
    }
    assert diff_baseline(old, new, console) is False


def test_changed_numbers_are_a_discrepancy(console):
    old = {"01.webm": {"boarded": 0, "alighted": 0}}
    new = {"01.webm": {"boarded": 0, "alighted": 1}}
    assert diff_baseline(old, new, console) is True


def test_disappeared_video_is_a_discrepancy(console):
    """Обратный случай трогать нельзя: пропажа видео — настоящий сигнал.

    Запись, которая раньше считалась, а теперь нет, означает потерянные данные
    или сломанный путь. Молчать об этом опаснее, чем лишний раз остановить push.
    """
    old = {"01.webm": {"boarded": 0, "alighted": 0}}
    assert diff_baseline(old, {}, console) is True


def test_identical_sets_are_quiet(console):
    same = {"01.webm": {"boarded": 1, "alighted": 2}}
    assert diff_baseline(same, dict(same), console) is False


# ---- «Разметка: авто» там, где разметки нет вовсе -------------------------
#
# Колонка в `paxcount info` показывала «авто» и при настоящем автопредложении, и
# при полном его отсутствии. Оператор, глядя на боевую запись, видит «авто» и
# считает, что двери размечены, — тогда как сработает фолбэк: одна зона на весь
# корпус, ловящая и прохожих вдоль борта.


def test_markup_state_distinguishes_three_cases(tmp_path, monkeypatch):
    from paxcount import doors

    zones = tmp_path / "zones"
    zones.mkdir()
    monkeypatch.setattr(doors, "ZONES_DIR", zones)

    (zones / "manual.json").write_text("{}", encoding="utf-8")
    (zones / "auto.auto.json").write_text("{}", encoding="utf-8")

    assert markup_state(tmp_path / "manual.webm") == "ручная"
    assert markup_state(tmp_path / "auto.webm") == "авто"
    assert markup_state(tmp_path / "nothing.webm") == "фолбэк"
