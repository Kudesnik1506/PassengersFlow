"""Два набора видео: отладочный и боевой.

Отладочный (`data/test_videos`) — ролики с Викисклада, на которых ловятся грубые
поломки логики и регрессии. Боевой (`data/prod_videos`) — записи с реальных
остановок, по которым и судят результат.

Разделение нужно не для порядка в файлах, а потому что смешивать их в одну
метрику бессмысленно: система предназначена для второго набора, а измеряется
пока по первому. Обе папки целиком в `.gitignore` — кадры содержат лица и
номера машин.
"""

from __future__ import annotations

import pytest
from paxcount import settings
from paxcount.settings import videos_in


@pytest.fixture
def two_sets(tmp_path, monkeypatch):
    """Подменяет оба каталога на временные, не трогая настоящие данные."""
    prod = tmp_path / "prod_videos"
    test = tmp_path / "test_videos"
    prod.mkdir()
    test.mkdir()
    monkeypatch.setattr(settings, "PROD_VIDEO_DIR", prod)
    monkeypatch.setattr(settings, "TEST_VIDEO_DIR", test)
    monkeypatch.setattr(settings, "VIDEO_DIRS", (prod, test))
    return prod, test


def test_single_file_returns_itself(tmp_path):
    video = tmp_path / "a.mp4"
    video.touch()
    assert videos_in(video) == [video]


def test_directory_returns_only_videos(two_sets):
    prod, _ = two_sets
    (prod / "b.webm").touch()
    (prod / "a.mp4").touch()
    (prod / "SOURCES.md").touch()  # не видео — в набор не попадает

    assert videos_in(prod) == [prod / "a.mp4", prod / "b.webm"]


def test_no_target_scans_both_sets_with_prod_first(two_sets):
    """Без аргумента берутся оба набора, боевой первым.

    Порядок не косметика: в отчётах и логах сначала должно идти то, по чему
    судят результат, а не отладочный материал.
    """
    prod, test = two_sets
    (test / "01_wikimedia.webm").touch()
    (prod / "stop_morning.mp4").touch()

    assert videos_in(None) == [prod / "stop_morning.mp4", test / "01_wikimedia.webm"]


def test_missing_directory_is_skipped(two_sets):
    """Боевой папки может ещё не быть — это не повод падать."""
    prod, test = two_sets
    (test / "01_wikimedia.webm").touch()
    prod.rmdir()

    assert videos_in(None) == [test / "01_wikimedia.webm"]


def test_both_directories_are_ignored_by_git():
    """Кадры содержат лица и номера машин — наружу уходят только числа.

    Проверяется сам `.gitignore`, а не намерение: правило легко потерять при
    правке, а цена ошибки необратима — опубликованное из истории не вернуть.
    """
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    rules = (root / ".gitignore").read_text(encoding="utf-8")
    assert "data/test_videos/*" in rules
    assert "data/prod_videos/*" in rules
