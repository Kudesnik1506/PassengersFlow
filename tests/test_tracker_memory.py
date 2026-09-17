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


# --- узнавание по внешности ------------------------------------------------
#
# Решения 057 и 058 закрыли всё, что двигает НОМЕР следа: частоту кадров, память
# трекера, сшивку и пороги детектора. Осталось единственное — узнавать человека
# по виду, а не по координате, когда он возвращается из-за перекрытия. В
# BoT-SORT это уже есть и выключено (`with_reid: False`).

def test_appearance_matching_enters_the_cache_key():
    """Треки с узнаванием и без — разные треки, общий кэш им нельзя."""
    assert DetectorSettings(with_reid=True).tag() != DetectorSettings().tag()


def test_appearance_matching_alone_generates_a_config():
    """Самая дорогая ошибка здесь — флаг, который молча ничего не включил.

    Порождение файла раньше заводилось только сменой памяти. Включить
    узнавание, не тронув память, означало бы получить штатный `botsort.yaml`,
    прогон без ReID и число, выданное за замер нового признака.
    """
    assert tracker_yaml(DetectorSettings(with_reid=True)) != "botsort.yaml"


def test_generated_yaml_turns_appearance_matching_on(tmp_path):
    path = tracker_yaml(DetectorSettings(with_reid=True), out_dir=tmp_path)
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert cfg["with_reid"] is True
    assert cfg["tracker_type"] == "botsort", "остальное берётся у исходного конфига"


def test_memory_and_appearance_do_not_share_a_file(tmp_path):
    """Два признака в одном имени: иначе прогоны перетрут друг друга."""
    only_memory = tracker_yaml(DetectorSettings(track_buffer=90), out_dir=tmp_path)
    both = tracker_yaml(DetectorSettings(track_buffer=90, with_reid=True),
                         out_dir=tmp_path)
    assert only_memory != both


# --- ворота узнавания ------------------------------------------------------
#
# Включённый ReID сам по себе почти ничего не меняет, и причина в `bot_sort.py`:
#
#     dists_mask = dists > (1 - self.proximity_thresh)
#     emb_dists[dists_mask] = 1.0
#
# внешность ВЫБРАСЫВАЕТСЯ везде, где рамки перекрываются меньше, чем на
# `proximity_thresh`. При штатных 0.5 узнавание работает уточнением между уже
# наложенными рамками и не способно вернуть человека, вышедшего из-за автобуса
# в стороне от места пропажи, — то есть ровно наш случай (решения 057, 058).
# Значит величина ворот обязана быть настраиваемой и обязана входить в ключ
# кэша: прогоны с разными воротами — разные треки.

def test_the_appearance_gate_enters_the_cache_key():
    reid = DetectorSettings(with_reid=True)
    assert reid.tag() != DetectorSettings(with_reid=True, proximity_thresh=0.1).tag()
    assert reid.tag() != DetectorSettings(with_reid=True, appearance_thresh=0.5).tag()


def test_the_appearance_gate_reaches_the_generated_config(tmp_path):
    path = tracker_yaml(
        DetectorSettings(with_reid=True, proximity_thresh=0.1, appearance_thresh=0.5),
        out_dir=tmp_path,
    )
    cfg = yaml.safe_load(Path(path).read_text(encoding="utf-8"))
    assert cfg["proximity_thresh"] == 0.1
    assert cfg["appearance_thresh"] == 0.5


def test_the_gate_alone_does_not_rename_existing_caches():
    """Ворота без узнавания инертны — имени кэша они менять не смеют."""
    assert DetectorSettings(proximity_thresh=0.1).tag() == DetectorSettings().tag()
