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


def test_missing_path_fails_loudly(tmp_path):
    """Несуществующий путь — ошибка, а не «одно видео с таким именем».

    Раньше `videos_in` возвращала сам путь, и дальше он шёл по конвейеру как
    видео. На практике это выглядело так: запущенный веб-UI держал в памяти
    путь к папке, которую переименовали, и вместо понятного сообщения отдавал
    500 — разбираться приходилось по логам сервера.
    """
    with pytest.raises(FileNotFoundError, match="нет такого"):
        videos_in(tmp_path / "нет-такого-файла.mp4")


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


# ---- Боевые записи лежат в подпапках -----------------------------------------
#
# Оператор отдаёт смену не россыпью, а деревом: `видео 1/Камера 1/Утро/*.MP4`.
# Плоский обход такого дерева не видит вовсе, и это не теоретическая придирка —
# на 22 распакованных боевых файла `videos_in` вернула пустой список, а гейт
# `pre-push` при этом отрапортовал «видео есть» по пятнадцати отладочным
# роликам и признал набор проверенным. Ошибка, которая утверждает, что работа
# сделана, дороже ошибки, которая падает.


def test_video_in_subdirectory_is_found(two_sets):
    """Съёмка приходит деревом «партия / камера / смена», а не плоской папкой."""
    prod, _ = two_sets
    nested = prod / "видео 1" / "Камера 1" / "Утро"
    nested.mkdir(parents=True)
    (nested / "a.mp4").touch()

    assert videos_in(prod) == [nested / "a.mp4"]


def test_uppercase_suffix_is_a_video(two_sets):
    """Камера пишет `.MP4` заглавными. Регистр расширения — не признак."""
    prod, _ = two_sets
    (prod / "b.MP4").touch()

    assert videos_in(prod) == [prod / "b.MP4"]


def test_appledouble_companions_are_not_videos(two_sets):
    """`._имя.mp4` — служебный спутник macOS на внешнем томе, а не запись.

    Проект лежит на `/Volumes`, где такие файлы заводятся сами. Рекурсивный
    обход подберёт их наравне с записями, и каждый станет «видео», которое
    не открывается.
    """
    prod, _ = two_sets
    (prod / "._a.mp4").touch()
    (prod / "a.mp4").touch()

    assert videos_in(prod) == [prod / "a.mp4"]


def test_hook_and_settings_agree_on_what_a_video_is():
    """Гейт и конвейер обязаны искать одно и то же.

    `pre-push` обходит дерево своим `find` — он должен работать на свежем
    клоне без установленного проекта, поэтому вызвать `videos_in` не может.
    Цена расхождения уже заплачена: `find -name '*.mp4'` не находил `.MP4`,
    и боевые записи были невидимы для гейта, пока он бодро сообщал об успехе.
    """
    import re
    from pathlib import Path

    from paxcount.settings import VIDEO_SUFFIXES

    hook = (Path(__file__).resolve().parents[1] / "tools/hooks/pre-push").read_text(
        encoding="utf-8"
    )
    flags = set(re.findall(r"-(i?name) '\*(\.\w+)'", hook))
    assert flags, "в хуке не нашлось поиска видео по расширениям"

    assert {suffix for _, suffix in flags} == set(VIDEO_SUFFIXES), (
        "список расширений в хуке разошёлся с VIDEO_SUFFIXES"
    )
    assert {flag for flag, _ in flags} == {"iname"}, (
        "хук обязан искать без учёта регистра: камера пишет .MP4"
    )
