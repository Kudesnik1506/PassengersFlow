"""Направление движения в кадре — свойство камеры, а не отдельного визита.

На остановке весь транспорт идёт в одну сторону. Значит, нос кузова не нужно
определять у каждой машины: он следует из направления движения механически.
Спрашивать разметчика про нос на каждом визите — тридцать раз просить ответ,
который не меняется, и тридцать раз давать шанс ошибиться зеркально.

Хранится на КАМЕРУ, а не на остановку. К2 и К3 стоят на одной опоре спина к
спине и смотрят навстречу: одно и то же движение выглядит в них зеркально.
Замерено на борте 7861 — в один и тот же момент (07:04:55 по шкале К2) К3
видит его борт во весь кадр, а К2 только передний край.

Чужую настройку камера не одалживает — то же правило, что у поправки часов
(`delivery/clocks.py`) и по той же причине: догадка здесь стоит зеркальной
нумерации дверей, а с ней неверного кода 5-8 в отчёте заказчику.

Спрашивается направление, а не нос, потому что направление наблюдаемо: любой
проезд его показывает. Нос у стоящей машины приходится угадывать по деталям
кузова, и на боевых кадрах это оказалось ненадёжно.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from pathlib import Path

from .doorprop.layout import NOSE_LEFT, NOSE_RIGHT

MOTION_LEFT_TO_RIGHT = "слева-направо"
MOTION_RIGHT_TO_LEFT = "справа-налево"
MOTIONS = (MOTION_LEFT_TO_RIGHT, MOTION_RIGHT_TO_LEFT)

_FIELDS = ("camera", "motion", "measured_by")

# Камеры, по которым ведётся счёт, — то есть те, что видят двери на стоянке.
# К1 сюда не входит: она в ста метрах вниз по ходу, опознаёт борт и задаёт
# порядок проездов, но стоянку от проезда не отличает (решение 030). Разметка
# дверей нужна ровно для нарезки кропа под счёт, поэтому размечать К1 — делать
# работу, которая никуда не пойдёт.
#
# Величина относится к геометрии ЭТОЙ остановки. На следующей съёмке (другая
# остановка) роли камер определяются заново — см. `invalidates_on` записи 046.
COUNTING_CAMERAS = frozenset({"2", "3"})


def counts(camera: str) -> bool:
    """Ведётся ли по этой камере счёт, то есть нужна ли ей разметка дверей."""
    return camera in COUNTING_CAMERAS


@dataclass(frozen=True)
class CameraMotion:
    """Куда едет транспорт в кадре этой камеры на этой остановке."""

    stop: str
    camera: str
    motion: str
    measured_by: str


def nose_from_motion(motion: str) -> str:
    """Нос по направлению движения. Неизвестное направление — ошибка, не догадка."""
    if motion == MOTION_LEFT_TO_RIGHT:
        return NOSE_RIGHT
    if motion == MOTION_RIGHT_TO_LEFT:
        return NOSE_LEFT
    raise ValueError(
        f"неизвестное направление движения {motion!r}, допустимы {MOTIONS}"
    )


def motion_for(stop: str, camera: str, records: list[CameraMotion]) -> str | None:
    """Направление для камеры. `None` — не задано, и это вопрос к человеку."""
    for row in records:
        if row.stop == stop and row.camera == camera:
            return row.motion
    return None


def nose_for(stop: str, camera: str, records: list[CameraMotion]) -> str | None:
    motion = motion_for(stop, camera, records)
    return nose_from_motion(motion) if motion else None


def path_for(stop: str, root: Path) -> Path:
    return root / f"{stop}.csv"


def load(path: Path) -> list[CameraMotion]:
    """Читает настройки. Отсутствующий файл — ещё не начатая таблица."""
    if not path.exists():
        return []
    stop = path.stem
    with path.open(encoding="utf-8", newline="") as f:
        return [
            CameraMotion(stop=stop, camera=row["camera"], motion=row["motion"],
                          measured_by=row["measured_by"])
            for row in csv.DictReader(f)
        ]


def save(path: Path, records: list[CameraMotion]) -> None:
    """Пишет настройки, оставляя по одной строке на камеру.

    Последняя запись о камере вытесняет прежнюю: камера могла переехать, а
    разметчик — ошибиться. Две строки об одной камере означали бы, что
    правильный ответ зависит от порядка чтения файла.
    """
    latest: dict[str, CameraMotion] = {}
    for row in records:
        latest[row.camera] = row
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for camera in sorted(latest):
            row = asdict(latest[camera])
            row.pop("stop")
            writer.writerow(row)
