"""Сводная таблица: разметка, эталонная строка, часы камер, результаты методов.

Таблица отвечает на один вопрос — где метод ошибается, — и поэтому строится
по дверям, а не по визитам. Строка «нашёл 3 из 3» ничего не объясняет; строка
«дверь 1 нашли восемь методов, дверь 3 — ни один» показывает, что теряется
именно дальняя дверь, а не метод плох вообще.

Три сшивки здесь делаются осторожно, каждая по своей причине:

* **время на соседней камере — пересчёт, не замер.** Поправка измерена по
  файлу, и к соседнему файлу той же камеры она не переносится: три независимые
  пары К3↔К2 дали 418, 419 и 428 с, а расстояние между соседними машинами
  бывает меньше этого разброса. Нет поправки для файла, накрывающего момент, —
  клетка пуста с причиной;
* **эталонная строка сшивается по времени той камеры, на которой сделана
  разметка.** Визит, размеченный на К3, может вообще не иметь замеренного
  времени К2;
* **дверь за краем кадра в знаменатель не идёт.** Метод не мог её найти, и
  «не нашёл» про неё — не результат измерения, а дефект съёмки.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from ..delivery.clocks import ClockRecord, from_reference
from ..delivery.timeline import FileSlot
# Импорт, а не копия (запрет 8): тем же порогом отличается «та же машина» от
# «другой» при поиске дублей разметки — это одна и та же длительность стоянки.
from ..truth import SAME_VISIT_GAP_S
from ..truth_rows import TruthRow
from .cases import Case
from .run import OK
from .score import DoorMatch, score_case

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class CameraTime:
    """Время визита на часах одной камеры — и чем оно получено."""

    camera: str
    moment: datetime | None
    file: str | None
    # Замер — только на той камере, на кадре которой человек видел машину.
    # Остальные камеры дают арифметику по поправке, и в таблице она называется
    # пересчётом, а не временем.
    measured: bool
    problem: str | None = None


@dataclass(frozen=True)
class MethodRun:
    """Одна строка выгрузки стенда: что метод предложил на одном визите."""

    method: str
    visit_key: str
    status: str
    note: str
    predicted: tuple[Box, ...]
    # Уверенности метода в том же порядке, что рамки. Пустые — метод их не
    # сообщает, и отбор «первые K» на нём произволен (bench/counting.py).
    scores: tuple[float, ...] = ()


def _slot_for(file: str, slots: list[FileSlot]) -> FileSlot | None:
    for slot in slots:
        if slot.name == file:
            return slot
    return None


def _covers(slot: FileSlot, moment: datetime) -> bool:
    return 0.0 <= (moment - slot.start).total_seconds() < slot.duration_s


def camera_times(
    reference: datetime,
    cameras: tuple[str, ...],
    records: list[ClockRecord],
    slots: list[FileSlot],
    marked_on: str,
) -> list[CameraTime]:
    """Время визита на часах каждой камеры по моменту на шкале К2.

    Перебираются записи поправок, а не файлы: поправка известна для файла, и
    только для файла, чей интервал накрывает получившийся момент, пересчёт
    законен. Перебор идёт от поправки к файлу, а не наоборот, потому что до
    применения поправки момент на часах этой камеры ещё неизвестен.
    """
    out: list[CameraTime] = []
    for camera in cameras:
        found: CameraTime | None = None
        for record in (r for r in records if r.camera == camera):
            moment = from_reference(reference, camera, record.file, records)
            slot = _slot_for(record.file, slots)
            if slot is None or not _covers(slot, moment):
                continue
            found = CameraTime(camera=camera, moment=moment, file=record.file,
                                measured=(camera == marked_on))
            break
        out.append(found or CameraTime(
            camera=camera, moment=None, file=None, measured=False,
            problem=(f"нет поправки для файла камеры {camera}, накрывающего этот "
                     "момент: поправка измеряется на файл, соседний файл той же "
                     "камеры её не одалживает"),
        ))
    return out


def match_row(camera: str, moment: datetime, rows: list[TruthRow]) -> TruthRow | None:
    """Эталонная строка этого визита — по времени той камеры, где он размечен.

    Порог — длительность стоянки: две записи дальше друг от друга это уже
    разные машины, а не одна с иначе прочитанным временем.
    """
    best: tuple[float, TruthRow] | None = None
    for row in rows:
        stamp = row.arrival(camera)
        if stamp is None:
            continue
        gap = abs((stamp - moment).total_seconds())
        if gap <= SAME_VISIT_GAP_S and (best is None or gap < best[0]):
            best = (gap, row)
    return best[1] if best else None


def door_matches(
    case: Case,
    runs: list[MethodRun],
    methods: tuple[str, ...] | None = None,
) -> tuple[dict[str, DoorMatch], ...]:
    """По одной записи на видимую дверь: что каждый метод ей сопоставил.

    Метод, который не отработал («не установлен», «ошибка»), сюда не попадает
    и промахом тоже не считается: свести эти исходы к нулю найденных значит
    выдать неустановленный пакет за метод, который посмотрел и не нашёл.

    Возвращается не «попал/не попал», а само сопоставление: при запасе кропа в
    140 px и проёме в 5 px «накрыл» стоит и при промахе в полкузова, и
    отличить одно от другого можно только смещением центра.
    """
    out: list[dict[str, DoorMatch]] = [{} for _ in case.doors_visible]
    for run in runs:
        if run.visit_key != case.visit_key or run.status != OK:
            continue
        if methods is not None and run.method not in methods:
            continue
        score = score_case(list(case.doors_visible), list(run.predicted))
        for i, match in enumerate(score.matches):
            out[i][run.method] = match
    return tuple(out)


def door_hits(
    case: Case,
    runs: list[MethodRun],
    methods: tuple[str, ...] | None = None,
) -> tuple[tuple[str, ...], ...]:
    """Только имена методов, попавших в дверь, — короткая форма `door_matches`."""
    return tuple(
        tuple(name for name, match in per_door.items() if match.hit)
        for per_door in door_matches(case, runs, methods)
    )


def load_runs(path: Path) -> list[MethodRun]:
    """Читает выгрузку стенда `out/bench_doors.json`."""
    if not path.exists():
        return []
    return [
        MethodRun(
            method=row["method"], visit_key=row["visit_key"], status=row["status"],
            note=row.get("note") or "",
            predicted=tuple(tuple(float(v) for v in box)
                             for box in row.get("predicted") or ()),
            scores=tuple(float(v) for v in row.get("scores") or ()),
        )
        for row in json.loads(path.read_text(encoding="utf-8"))
    ]
