"""Часы камер: поправка к общей шкале — на файл, а не на камеру.

Ноль шкалы — К2, и выбор не произвольный: К2 — единственная камера, на
которой виден самый ранний по времени визит эталонной таблицы (маршрут 26,
борт 7326). У К1 и К3 часы идут иначе относительно неё, и не на одну и ту же
величину от файла к файлу: три независимые пары событий К3↔К2 за одно утро
дали −419 / −418 / −428 с. Называть это одной поправкой на камеру — подгонка
того же рода, что подгонка счёта под выгрузку: число похоже на правду, а
откуда оно взялось, не видно.

Отсюда разделение на два объекта:

* `Measurement` — сырая пара одновременных событий на двух камерах. По набору
  таких пар считается разброс (`spread_s`) — и только он говорит, можно ли уже
  считать поправку константой (`is_ready`, порог 2 с, план шаг 0а).
* `ClockRecord` — принятая для конкретного файла поправка, которой пользуется
  остальной код (`to_reference`/`from_reference`). Она может быть построена по
  одному замеру или по среднему из нескольких — это решает тот, кто её вносит
  в `data/clocks/<остановка>.csv`, а не этот модуль.

Поправка нарочно не оптова: `to_reference` отказывается смотреть поправку
соседнего файла той же камеры, даже если она есть. Один пропущенный по камере
файл — это дыра в таблице, а не повод для догадки.
"""

from __future__ import annotations

import csv
from dataclasses import asdict, dataclass
from datetime import datetime, timedelta
from pathlib import Path

# Порог готовности из плана (шаг 0а): поправка считается константой, если
# остаток по всем контрольным парам одной камеры не превышает эту величину.
READY_RESIDUAL_S = 2.0

_FIELDS = ("camera", "file", "offset_to_k2_s", "measured_by", "date_override")


@dataclass(frozen=True)
class ClockRecord:
    """Поправка одного файла к шкале К2: raw_time − offset = reference_time."""

    camera: str
    file: str
    offset_to_k2_s: float
    measured_by: str
    # Дата в имени файла верна не у всех камер (у К3 на боевой записи — нет,
    # это август вместо сентября). Пусто — дата из имени верна как есть.
    date_override: str | None = None


@dataclass(frozen=True)
class Measurement:
    """Одна пара одновременных событий на двух камерах — сырьё для поправки.

    Событие видно на обеих камерах в один физический момент (кузов пересекает
    границу кадров у опоры, машина отъезжает и т.д.). `reference_time` — то же
    событие, прочитанное на шкале К2.
    """

    camera: str
    file: str
    raw_time: datetime
    reference_time: datetime

    @property
    def offset_to_k2_s(self) -> float:
        return (self.raw_time - self.reference_time).total_seconds()


def spread_s(measurements: list[Measurement]) -> float:
    """Разброс поправки по независимым парам одной камеры.

    Меньше двух пар разброс не имеет смысла: единственный замер (как у К1,
    полученный через допущение о ходе машины, а не независимой парой) не с чем
    сравнить — `is_ready` на нём был бы правдоподобной, но пустой единицей.
    """
    if len(measurements) < 2:
        raise ValueError(
            "разброс поправки нужен минимум по двум независимым парам, "
            f"дано {len(measurements)}"
        )
    offsets = [m.offset_to_k2_s for m in measurements]
    return max(offsets) - min(offsets)


def is_ready(measurements: list[Measurement]) -> bool:
    """Можно ли считать поправку константой, а не диапазоном на глаз."""
    return spread_s(measurements) <= READY_RESIDUAL_S


def to_reference(moment: datetime, camera: str, file: str,
                  records: list[ClockRecord]) -> datetime:
    """Переводит время на часах камеры в момент на шкале К2.

    Дата чинится вместе со временем. У К3 в именах файлов стоит август вместо
    сентября, и без этого визит одной машины расходился по камерам на месяц:
    борт 7861 ложился на 2026-08-10 07:05:04 против 2026-09-10 07:05:00 на К2 —
    время суток сходилось, а сшить визит было нечем.
    """
    row = _record_for(camera, file, records)
    return _with_real_date(moment, row) - timedelta(seconds=row.offset_to_k2_s)


def from_reference(moment: datetime, camera: str, file: str,
                    records: list[ClockRecord]) -> datetime:
    """Обратный перевод: момент на шкале К2 → время на часах камеры.

    Возвращает дату, которая стоит В ИМЕНИ ФАЙЛА, а не настоящую: по этому
    времени файл ищут и перематывают, и «починенная» дата там помешала бы.
    Дату имени берёт `parse_slot`, а не собственный разбор строки, — соглашение
    об именах живёт в одном месте.
    """
    row = _record_for(camera, file, records)
    raw = moment + timedelta(seconds=row.offset_to_k2_s)
    if row.date_override is None:
        return raw
    from .timeline import parse_slot

    named = parse_slot(row.file).date
    return raw.replace(year=named.year, month=named.month, day=named.day)


def _with_real_date(moment: datetime, row: ClockRecord) -> datetime:
    """Подменяет дату на настоящую, если имя файла её врёт."""
    if row.date_override is None:
        return moment
    real = datetime.strptime(row.date_override, "%Y-%m-%d").date()
    return moment.replace(year=real.year, month=real.month, day=real.day)


def _record_for(camera: str, file: str, records: list[ClockRecord]) -> ClockRecord:
    for row in records:
        if row.camera == camera and row.file == file:
            return row
    raise LookupError(
        f"нет замера поправки для камеры {camera}, файла {file!r} — "
        "поправка на файл, соседний файл той же камеры её не одалживает"
    )


def load(path: Path) -> list[ClockRecord]:
    """Читает таблицу поправок. Отсутствующий файл — ещё не начатая таблица."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [
            ClockRecord(
                camera=row["camera"],
                file=row["file"],
                offset_to_k2_s=float(row["offset_to_k2_s"]),
                measured_by=row["measured_by"],
                date_override=row["date_override"] or None,
            )
            for row in csv.DictReader(f)
        ]


def save(path: Path, records: list[ClockRecord]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=_FIELDS)
        writer.writeheader()
        for row in records:
            writer.writerow({**asdict(row), "date_override": row.date_override or ""})
