"""Качество следов людей: рвутся они или поглощаются.

Прибор, а не правило счёта. Нужен, чтобы отличить два исхода, которые в счёте
выглядят одинаково — недосчётом, — а лечатся противоположным.

Решение 001 замерило: «теряется не обнаружение, а удержание личности» — 10
обрывков по 2-14 кадров против 7 полноценных следов. Решение 002 уточнило
механизм: «человек не разрывается надвое, а поглощается перекрытием внутри
чужого трека». Если преобладает разрыв, чаще смотреть кадры — помогает: у
трекера больше наблюдений, чтобы удержать личность. Если поглощение — частота
кадров ни при чём, человека не потеряли, его слили с соседом.

Три исхода смерти следа:

* **передача личности** (`handovers`) — рядом по времени и месту родился другой
  след. Это разрыв: тот же человек продолжился под новым номером;
* **поглощение** (`vanishings`) — никто рядом не родился, и след оборвался не у
  края кадра. Человек растворился в чужой рамке либо пропал для детектора;
* **уход за край кадра** (`at_border`) — не потеря вовсе. Смешать его с
  поглощением значит записать в потери каждого, кто просто вышел из поля
  зрения, и получить бодрое число, которое ничего не значит.

Отдельно считается `alive_at_edge` — следы, дожившие до конца окна. Они не
умирали, окно кончилось раньше; в потери они не идут.

Пороги соседства импортируются из `zonecount` (запрет 9). «Рядом по времени и
месту» здесь означает ровно то же, что там, где на этом же признаке снимается
событие, и разъехаться эти два понятия не должны.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .core.trackdata import TrackData
from .zonecount import (
    BORDER_MARGIN, MIN_TRACK_SECONDS, SWITCH_RADIUS, SWITCH_SECONDS,
)


@dataclass(frozen=True)
class TrackStats:
    """Сводка по одному окну. Все числа — про следы, а не про пассажиров."""

    tracks: int
    fragments: int          # короче MIN_TRACK_SECONDS
    median_seconds: float
    handovers: int          # оборвался, рядом родился другой — разрыв
    vanishings: int         # оборвался без наследника не у края — поглощение
    at_border: int          # ушёл за край кадра — законный уход из поля зрения
    alive_at_edge: int      # дожил до конца окна — окно кончилось, не он

    @property
    def losses(self) -> int:
        return self.handovers + self.vanishings


Zone = tuple[float, float, float, float]


@dataclass(frozen=True)
class _Life:
    tid: int
    first_ts: float
    last_ts: float
    first_anchor: tuple[float, float]
    last_anchor: tuple[float, float]
    last_box: np.ndarray

    @property
    def seconds(self) -> float:
        return self.last_ts - self.first_ts


def _anchor(box: np.ndarray) -> tuple[float, float]:
    return float((box[0] + box[2]) / 2), float(box[3])


def _inside(anchor: tuple[float, float], zone: Zone) -> bool:
    return zone[0] <= anchor[0] <= zone[2] and zone[1] <= anchor[1] <= zone[3]


def _lives(data: TrackData, zones: list[Zone] | None = None) -> list[_Life]:
    first: dict[int, tuple[float, np.ndarray]] = {}
    last: dict[int, tuple[float, np.ndarray]] = {}
    touched: set[int] = set()
    for f in data.frames:
        for tid, box in zip(f.person_ids, f.person_boxes):
            tid = int(tid)
            if tid not in first:
                first[tid] = (f.ts, box)
            last[tid] = (f.ts, box)
            if zones and any(_inside(_anchor(box), z) for z in zones):
                touched.add(tid)
    if zones is not None:
        first = {tid: v for tid, v in first.items() if tid in touched}
    return [
        _Life(
            tid=tid,
            first_ts=first[tid][0], last_ts=last[tid][0],
            first_anchor=_anchor(first[tid][1]), last_anchor=_anchor(last[tid][1]),
            last_box=last[tid][1],
        )
        for tid in sorted(first)
    ]


def _at_border(box: np.ndarray, width: int, height: int) -> bool:
    return bool(
        box[0] <= BORDER_MARGIN
        or box[1] <= BORDER_MARGIN
        or box[2] >= width - BORDER_MARGIN
        or box[3] >= height - BORDER_MARGIN
    )


def track_stats(
    data: TrackData,
    zones: list[Zone] | None = None,
    gap_seconds: float = SWITCH_SECONDS,
    radius_ratio: float = SWITCH_RADIUS,
    min_seconds: float = MIN_TRACK_SECONDS,
) -> TrackStats:
    """Сводка по следам окна. Считается на СЫРЫХ треках, до сшивки.

    До сшивки намеренно: сшивка чинит часть разрывов, и на её выходе не видно,
    сколько их было. Сколько она починила, показывает `build_remap` отдельно.

    ``zones`` сужает набор до следов, хоть раз побывавших в дверной зоне. Без
    этого на боевой остановке считается людность улицы, а не работа трекера:
    сотни прохожих в кадре к счёту пассажиров отношения не имеют. Наследники
    при этом ищутся среди ТЕХ ЖЕ отобранных следов — сосед, не подходивший к
    двери, объяснением обрыва у двери не служит.
    """
    lives = _lives(data, zones)
    if not lives:
        return TrackStats(0, 0, 0.0, 0, 0, 0, 0)

    window_end = max(f.ts for f in data.frames)
    handovers = vanishings = at_border = alive = 0

    for life in lives:
        # Дожил до конца окна: окно кончилось раньше человека, это не смерть.
        if window_end - life.last_ts <= gap_seconds:
            alive += 1
            continue
        if _at_border(life.last_box, data.width, data.height):
            at_border += 1
            continue
        radius = radius_ratio * max(float(life.last_box[3] - life.last_box[1]), 1.0)
        heir = any(
            other.tid != life.tid
            and 0.0 <= other.first_ts - life.last_ts <= gap_seconds
            and np.hypot(other.first_anchor[0] - life.last_anchor[0],
                          other.first_anchor[1] - life.last_anchor[1]) <= radius
            for other in lives
        )
        if heir:
            handovers += 1
        else:
            vanishings += 1

    spans = sorted(life.seconds for life in lives)
    return TrackStats(
        tracks=len(lives),
        fragments=sum(1 for life in lives if life.seconds < min_seconds),
        median_seconds=float(np.median(spans)),
        handovers=handovers,
        vanishings=vanishings,
        at_border=at_border,
        alive_at_edge=alive,
    )
