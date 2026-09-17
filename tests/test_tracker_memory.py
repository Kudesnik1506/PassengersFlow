"""Память трекера: сколько кадров он держит потерянного человека.

Величина живёт в конфиге ultralytics (`botsort.yaml`, `track_buffer: 30`) и
считается в ОБРАБОТАННЫХ кадрах, а не в секундах: `byte_tracker.py` берёт её как
`max_frames_lost = args.track_buffer`, без пересчёта по частоте и без предела
сверху. Поэтому при шаге детекции 3 тридцать кадров — это три секунды, а при
шаге 1 — одна.

Отсюда обе проверки ниже. Первая: стенд, перейдя на покадровость, обязан
получить память, равную боевым трём секундам, иначе переход на каждый кадр
ВТРОЕ укоротит её и померяет не то. Вторая: величина обязана войти в ключ кэша,
иначе треки, посчитанные при другой памяти, молча сойдут за свои — тот самый
дефект, ради которого заведено решение 022.

Третья проверка — про цену: поле не смеет переименовать нынешние кэши. Их
содержимое от появления поля не изменилось, и платить за это передетекцией
боевых записей незачем.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from paxcount.core.detect import tracker_yaml
from paxcount.settings import DEFAULT_TRACK_BUFFER, DetectorSettings


def test_tracker_memory_enters_the_cache_key():
    """Другая память — другой кэш: иначе чужие треки сойдут за свои."""
    assert DetectorSettings(track_buffer=90).tag() != DetectorSettings().tag()


def test_default_memory_keeps_every_existing_cache_name():
    """Появление поля не смеет обесценить уже посчитанные кэши."""
    assert DetectorSettings().tag() == "yolo11s_960_botsort_s1_c0.25"
    assert DetectorSettings(stride=3).tag() == "yolo11s_960_botsort_s3_c0.25"


def test_the_bench_keeps_the_prod_memory_in_real_time():
    """Покадровость втрое укорачивает память — если не поправить число.

    Боевой путь: шаг 3, память 30 кадров = 3 секунды. Стенд переходит на шаг 1,
    и чтобы остались те же три секунды, кадров нужно 90. Это не ослабление
    (запрет 5), а удержание величины постоянной при смене единицы.
    """
    prod = DetectorSettings(stride=3)
    bench = DetectorSettings(stride=1, track_buffer=90)
    assert prod.track_buffer == DEFAULT_TRACK_BUFFER == 30
    assert prod.memory_seconds(fps=30.0) == bench.memory_seconds(fps=30.0) == 3.0


def test_generated_yaml_carries_the_memory_and_keeps_the_rest(tmp_path):
    """Ultralytics принимает только путь к файлу, значит файл надо породить."""
    path = tracker_yaml(DetectorSettings(track_buffer=90), out_dir=tmp_path)
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert cfg["track_buffer"] == 90
    assert cfg["tracker_type"] == "botsort", "остальное берётся у исходного конфига"


def test_default_memory_needs_no_generated_file():
    """При умолчании отдаётся штатный botsort.yaml — лишних файлов не плодим."""
    assert tracker_yaml(DetectorSettings()) == "botsort.yaml"
