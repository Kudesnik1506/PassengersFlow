"""Единственный источник правды по путям и настройкам детектора.

Раньше «модель yolo11s, imgsz 960, трекер botsort» были прописаны в четырёх
местах: cli, webui, tools/strip и core/detect. Такие копии не ловятся ни
линтером, ни тестами — каждая корректна сама по себе, но при смене модели
кэш-ключи разъезжаются, и половина потребителей молча читает чужой кэш.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

DATA_DIR = Path("data")
VIDEO_DIR = DATA_DIR / "videos"
ZONES_DIR = DATA_DIR / "zones"
# Синтетические ролики (склейки набора) — не съёмка и не часть метрик, но их
# разметка лежит в общем ZONES_DIR, поэтому проверке набора надо знать, где
# искать соответствующее видео.
SYNTHETIC_DIR = DATA_DIR / "synthetic"
CACHE_DIR = DATA_DIR / "cache"

# Границы вертикальной полосы дверной зоны в долях высоты рамки ТС.
#
# Зона в режиме `zone` описывает не высоту дверного проёма, а полосу, где может
# находиться ТОЧКА ОПОРЫ человека — ноги. Замер по 15 роликам (7116 наблюдений
# людей у корпуса): ноги в 90% случаев ниже 0.66 высоты рамки, медиана 1.12
# (люди ближе к камере, чем ТС, оказываются ниже его рамки).
#
# Низ: 0.95, а не 1.0 — у линейных дверей на платформе (04) порог двери реально
# лежит внутри рамки состава, и требовать «ниже колёс» там неверно.
# Верх: 0.60 — выше начинается салон, и зона ловит пассажиров, видимых сквозь
# дверь: они дают ложные события (см. README, «Полоса ног, а не дверной проём»).
#
# Лежат здесь, а не в cli.py, чтобы их видел и гейт tools/hooks/gate_zones.py:
# он работает без зависимостей проекта (в CI нет ни torch, ни видео), а
# продублированный порог разъехался бы молча.
ZONE_MIN_BOTTOM = 0.95
ZONE_MIN_TOP = 0.60
# Допустимый диапазон долей зоны: за его пределами почти наверняка опечатка.
ZONE_FRACTION_MIN = -0.5
ZONE_FRACTION_MAX = 1.5
TRUTH_PATH = DATA_DIR / "truth" / "truth.csv"
OUT_DIR = Path("out")
LOG_DIR = Path("logs")
MODELS_DIR = Path("models")

VIDEO_SUFFIXES = frozenset({".mp4", ".webm", ".ogv", ".mov", ".avi", ".mkv"})

# Порог приёмки из плана: суммарная погрешность по входу и выходу.
TARGET_ERROR = 0.10


@dataclass(frozen=True)
class DetectorSettings:
    """Всё, что влияет на результат детекции, а значит и на ключ кэша."""

    weights: str = str(MODELS_DIR / "yolo11s.pt")
    imgsz: int = 960
    # botsort включает компенсацию движения камеры — нужна для съёмки с рук.
    tracker: str = "botsort.yaml"
    stride: int = 1
    conf: float = 0.25

    def tag(self) -> str:
        return (
            f"{Path(self.weights).stem}_{self.imgsz}_"
            f"{Path(self.tracker).stem}_s{self.stride}_c{self.conf}"
        )


DETECTOR = DetectorSettings()


@dataclass(frozen=True)
class DoorProposalSettings:
    """Версия формата автопредложения дверей — часть ключа кэша .auto.json.

    Смена алгоритма (постфильтра, промптов, параметров агрегации) должна
    сделать старый файл предложения нечитаемым как валидный текущий, а не
    молча подсунуть числа от прошлой версии логики.
    """

    version: int = 1
    reference_frames: int = 9


DOOR_PROPOSAL = DoorProposalSettings()


def videos_in(target: Path) -> list[Path]:
    """Видео по пути: сам файл или всё подходящее в каталоге."""
    if target.is_dir():
        return sorted(p for p in target.iterdir() if p.suffix.lower() in VIDEO_SUFFIXES)
    return [target]
