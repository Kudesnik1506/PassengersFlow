"""Зоны дверей: приоритетная цепочка «ручная правка → автопредложение → фолбэк».

Гибридный режим из интервью: система сама предлагает зоны дверей, человек
правит их в веб-UI, правка сохраняется в data/zones/<stem>.json и с этого
момента побеждает автопредложение.

``load_config()`` дёргается из шести мест и обязана оставаться дешёвой —
читает готовый JSON, и только. Сама детекция дверей стоит секунды (пиксели
видео плюс вес модели), поэтому она вынесена в явный шаг ``paxcount doors
propose`` и кнопку веб-UI: иначе любой вызывающий внезапно начинал бы платить
за то, чего не просил.
"""

from __future__ import annotations

import json
from pathlib import Path

from .core.trackdata import content_hash
from .core.types import DoorSpec, Point, VideoConfig
from .settings import DOOR_PROPOSAL, ZONES_DIR


def fallback_doors() -> list[DoorSpec]:
    """Последняя линия обороны: одна зона на весь корпус ТС.

    Раньше это же значение возвращала ``auto_door()`` и выдавалось за
    автопредложение — из-за чего система предлагала одну зону там, где дверей
    две или три. Теперь это честно названо фолбэком: он применяется, только
    когда ни ручной разметки, ни автопредложения нет.
    """
    return [
        DoorSpec(
            door_id="fallback",
            frame="vehicle",
            line_start=Point(x=0.0, y=1.0),
            line_end=Point(x=1.0, y=1.0),
            inside_sign=-1,
            band=0.25,
        )
    ]


def config_path(video: Path) -> Path:
    """Ручная разметка — то, что нарисовал человек."""
    return ZONES_DIR / f"{video.stem}.json"


def auto_config_path(video: Path) -> Path:
    """Текущее автопредложение, которое читает load_config()."""
    return ZONES_DIR / f"{video.stem}.auto.json"


def proposal_cache_path(video: Path, method_tag: str) -> Path:
    """Версионированный кэш предложения конкретного метода.

    Имя несёт версию формата и хеш содержимого видео: подмена файла видео или
    смена алгоритма не должны читаться как валидный прошлый результат.
    """
    return ZONES_DIR / (
        f"{video.stem}.auto__p{DOOR_PROPOSAL.version}_{method_tag}_"
        f"{content_hash(video)}.json"
    )


def load_config(video: Path) -> VideoConfig:
    """Приоритетная цепочка: ручное → автопредложение → фолбэк."""
    path = config_path(video)
    if path.exists():
        cfg = VideoConfig.model_validate_json(path.read_text(encoding="utf-8"))
        cfg.manual = True
        return cfg

    auto = auto_config_path(video)
    if auto.exists():
        cfg = VideoConfig.model_validate_json(auto.read_text(encoding="utf-8"))
        cfg.manual = False
        return cfg

    cfg = VideoConfig.default_for(video)
    cfg.doors = fallback_doors()
    return cfg


def save_config(cfg: VideoConfig) -> Path:
    ZONES_DIR.mkdir(parents=True, exist_ok=True)
    path = ZONES_DIR / f"{Path(cfg.video).stem}.json"
    path.write_text(
        json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return path


def save_proposal(cfg: VideoConfig, video: Path, method_tag: str) -> Path:
    """Пишет предложение и в версионированный кэш, и в файл для load_config()."""
    ZONES_DIR.mkdir(parents=True, exist_ok=True)
    payload = json.dumps(cfg.model_dump(), ensure_ascii=False, indent=2)
    proposal_cache_path(video, method_tag).write_text(payload, encoding="utf-8")
    auto = auto_config_path(video)
    auto.write_text(payload, encoding="utf-8")
    return auto


def is_manual(video: Path) -> bool:
    return config_path(video).exists()


def markup_state(video: Path) -> str:
    """Что на самом деле будет считать двери этого ролика.

    Три состояния, а не два. Раньше `paxcount info` печатал «авто» и при
    настоящем автопредложении, и при полном отсутствии разметки — а это разные
    вещи: во втором случае работает фолбэк, одна зона на весь корпус, которая
    ловит и прохожих вдоль борта. Оператор, глядя на боевую запись, видел
    «авто» и считал, что двери размечены.
    """
    if config_path(video).exists():
        return "ручная"
    if auto_config_path(video).exists():
        return "авто"
    return "фолбэк"
