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


# ---- Боевая запись без кэша не должна запускать детекцию из гейтов --------
#
# Гейты (`doors jitter`, `baseline`) вызываются автоматически на каждый push.
# Девятичасовая запись без готового кэша, случайно попавшая в data/prod_videos,
# не должна САМА запустить детекцию внутри обычного `git push` — это часы
# работы там, где ожидались секунды.


def test_prod_video_gets_reduced_stride():
    from paxcount.settings import DETECTOR, PROD_STRIDE, PROD_VIDEO_DIR, detector_for

    video = PROD_VIDEO_DIR / "2026-09-10_stop.mp4"
    assert detector_for(video).stride == PROD_STRIDE
    assert PROD_STRIDE != DETECTOR.stride


def test_test_video_keeps_default_stride():
    from paxcount.settings import DETECTOR, TEST_VIDEO_DIR, detector_for

    video = TEST_VIDEO_DIR / "01_liaz6213_tyumen_doors_open.webm"
    assert detector_for(video).stride == DETECTOR.stride


def test_prod_stride_changes_cache_key():
    """Боевой и тестовый кэш не должны путаться: stride входит в tag()."""
    from paxcount.settings import PROD_VIDEO_DIR, TEST_VIDEO_DIR, detector_for

    prod = detector_for(PROD_VIDEO_DIR / "a.mp4")
    test = detector_for(TEST_VIDEO_DIR / "a.mp4")
    assert prod.tag() != test.tag()


def test_prod_cache_missing_is_true_only_for_uncached_prod_video(tmp_path, monkeypatch):
    from paxcount import settings as settings_mod

    prod_dir = tmp_path / "prod_videos"
    prod_dir.mkdir()
    test_dir = tmp_path / "test_videos"
    test_dir.mkdir()
    monkeypatch.setattr(settings_mod, "PROD_VIDEO_DIR", prod_dir)

    prod_video = prod_dir / "9h.mp4"
    test_video = test_dir / "01.webm"

    # Ни один физически не существует и кэша ни у кого нет — но предикат
    # обязан различать боевое видео (пропустить) и тестовое (не его забота).
    assert settings_mod.prod_cache_missing(prod_video) is True
    assert settings_mod.prod_cache_missing(test_video) is False


def test_baseline_skips_uncached_prod_video(tmp_path, monkeypatch):
    """`paxcount baseline` не должен сам запускать детекцию на боевой записи
    без кэша — иначе обычный push однажды превращается в многочасовой прогон.

    `paxcount.tracking` тянет torch, которого нет в лёгкой группе `test` —
    та же причина, по которой `compute_baseline` импортирует его только внутри
    функции. В CI (``--only-group test``) тест пропускается; в pre-push
    (``--group test``, полный venv) выполняется по-настоящему.
    """
    pytest.importorskip("torch")
    from paxcount import baseline as baseline_mod
    from paxcount import settings as settings_mod
    from paxcount import tracking as tracking_mod

    prod_dir = tmp_path / "prod_videos"
    prod_dir.mkdir()
    video = prod_dir / "9h_recording.mp4"
    video.write_bytes(b"fake-bytes-not-a-real-video")

    monkeypatch.setattr(settings_mod, "PROD_VIDEO_DIR", prod_dir)

    def _boom(*args, **kwargs):
        raise AssertionError(
            "детекция не должна запускаться для боевого видео без кэша"
        )

    monkeypatch.setattr(tracking_mod, "get_tracks", _boom)

    result = baseline_mod.compute_baseline(prod_dir)
    assert result == {}


def test_doors_jitter_skips_uncached_prod_video(tmp_path, monkeypatch):
    """`paxcount.cli` тянет typer, которого нет в лёгкой группе `test` —
    пропускается в CI, выполняется по-настоящему в pre-push."""
    pytest.importorskip("typer")
    from paxcount import cli as cli_mod
    from paxcount import settings as settings_mod

    prod_dir = tmp_path / "prod_videos"
    prod_dir.mkdir()
    video = prod_dir / "9h_recording.mp4"
    video.write_bytes(b"fake-bytes-not-a-real-video")

    monkeypatch.setattr(settings_mod, "PROD_VIDEO_DIR", prod_dir)

    def _boom(*args, **kwargs):
        raise AssertionError(
            "детекция не должна запускаться для боевого видео без кэша"
        )

    monkeypatch.setattr(cli_mod, "get_tracks", _boom)

    cli_mod.doors_jitter(prod_dir)  # не должно поднять AssertionError выше


# ---- Боевой stride доходит до самой детекции ---------------------------------


def test_cli_settings_take_stride_from_the_video_set(tmp_path, monkeypatch):
    """Без явной опции stride берётся по набору, а не из умолчания команды.

    Дефект был не теоретический: первый прогон на боевой записи прошёл со
    stride 1, потому что опция `--stride` имеет умолчание и молча перебивала
    `detector_for`. Детекция заняла втрое больше нужного, а кэш лёг с ключом
    `s1` — то есть `prod_cache_missing`, которая спрашивает про `s3`, сочла бы
    его отсутствующим и запустила бы всё заново. Ровно от этого расхождения
    предостерегает докстринг `detector_for` и ради него написано решение 022.
    """
    from paxcount import cli, settings

    prod = tmp_path / "prod"
    prod.mkdir()
    monkeypatch.setattr(settings, "PROD_VIDEO_DIR", prod)
    video = prod / "смена.mp4"
    video.touch()

    chosen = cli._settings(
        settings.DETECTOR.weights, settings.DETECTOR.imgsz,
        settings.DETECTOR.tracker, None, video,
    )
    assert chosen.stride == settings.PROD_STRIDE


def test_cli_settings_still_honour_an_explicit_stride(tmp_path, monkeypatch):
    """Явное `--stride 1` на боевом видео остаётся правом человека."""
    from paxcount import cli, settings

    prod = tmp_path / "prod"
    prod.mkdir()
    monkeypatch.setattr(settings, "PROD_VIDEO_DIR", prod)
    video = prod / "смена.mp4"
    video.touch()

    chosen = cli._settings(
        settings.DETECTOR.weights, settings.DETECTOR.imgsz,
        settings.DETECTOR.tracker, 1, video,
    )
    assert chosen.stride == 1
