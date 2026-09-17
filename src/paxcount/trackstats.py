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


def _area(box) -> float:
    return float(max(box[2] - box[0], 0.0) * max(box[3] - box[1], 0.0))


def _outcome(life: _Life, lives: list[_Life], data: TrackData, window_end: float,
              gap_seconds: float, radius_ratio: float) -> str:
    """Чем кончился след: alive / border / handover / vanishing.

    Одна функция на оба прибора намеренно: разбор поглощений обязан считать
    поглощением ровно то же, что и сводка. Разъедутся — доли будут посчитаны
    от разных знаменателей, и выбор лечения окажется случайным.
    """
    # Дожил до конца окна: окно кончилось раньше человека, это не смерть.
    if window_end - life.last_ts <= gap_seconds:
        return "alive"
    if _at_border(life.last_box, data.width, data.height):
        return "border"
    radius = radius_ratio * max(float(life.last_box[3] - life.last_box[1]), 1.0)
    heir = any(
        other.tid != life.tid
        and 0.0 <= other.first_ts - life.last_ts <= gap_seconds
        and np.hypot(other.first_anchor[0] - life.last_anchor[0],
                      other.first_anchor[1] - life.last_anchor[1]) <= radius
        for other in lives
    )
    return "handover" if heir else "vanishing"


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
    tally = {"alive": 0, "border": 0, "handover": 0, "vanishing": 0}
    for life in lives:
        tally[_outcome(life, lives, data, window_end, gap_seconds, radius_ratio)] += 1
    handovers, vanishings = tally["handover"], tally["vanishing"]
    at_border, alive = tally["border"], tally["alive"]

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


# Во сколько раз обязана вырасти рамка соседа, чтобы счесть её слиянием.
# Двое стоящих вплотную дают рамку примерно вдвое шире одиночной, так что 1.2 —
# осторожный низ: дыхание рамки одного человека за слияние не сойдёт, а
# настоящая склейка двоих пройдёт с запасом.
MERGE_GROWTH = 1.2


@dataclass(frozen=True)
class AbsorptionSplit:
    """Из чего состоят поглощения. Лечение выбирается по большинству."""

    merged: int      # место накрыла ВЫРОСШАЯ рамка соседа — двое в одной рамке
    occluded: int    # место накрыто чужой рамкой, но она не росла — заслонили
    invisible: int   # на месте нет ничьей рамки — детектор не выдаёт ничего

    @property
    def total(self) -> int:
        return self.merged + self.occluded + self.invisible


def _kind_of_absorption(data: TrackData, life: _Life, growth: float) -> str:
    """Чем накрыто место исчезнувшего на СЛЕДУЮЩЕМ кадре.

    Точка опоры берётся та же, по которой решается принадлежность зоне: у
    стоящего дальше от камеры ноги выше на кадре, поэтому его опора попадает
    внутрь рамки того, кто стоит ближе и его заслоняет.
    """
    idx = next((k for k, f in enumerate(data.frames) if f.ts == life.last_ts), None)
    if idx is None or idx + 1 >= len(data.frames):
        return "invisible"          # следующего кадра нет — судить не по чему
    dying, after = data.frames[idx], data.frames[idx + 1]
    was = {int(t): _area(b) for t, b in zip(dying.person_ids, dying.person_boxes)}

    covering = [
        (int(tid), box) for tid, box in zip(after.person_ids, after.person_boxes)
        if int(tid) != life.tid and _inside(life.last_anchor, tuple(box))
    ]
    if not covering:
        return "invisible"
    # Хватает одной выросшей: слияние — утверждение о конкретной рамке, и
    # присутствие рядом второй, не выросшей, его не отменяет.
    if any(tid in was and _area(box) >= growth * was[tid] for tid, box in covering):
        return "merged"
    return "occluded"


def absorption_kinds(
    data: TrackData,
    zones: list[Zone] | None = None,
    gap_seconds: float = SWITCH_SECONDS,
    radius_ratio: float = SWITCH_RADIUS,
    growth: float = MERGE_GROWTH,
) -> AbsorptionSplit:
    """Разбор поглощений на механизмы. Считается на СЫРЫХ треках, до сшивки.

    Вопрос, ради которого прибор существует: чинить детектор или трекер.
    Слияние — про подавление дубликатов в детекторе, он человека видит.
    Невидимость и заслон — про узнавание по внешности, детектор не видит
    ничего, и порогами NMS этого не поправить.
    """
    lives = _lives(data, zones)
    if not lives:
        return AbsorptionSplit(0, 0, 0)
    window_end = max(f.ts for f in data.frames)
    tally = {"merged": 0, "occluded": 0, "invisible": 0}
    for life in lives:
        if _outcome(life, lives, data, window_end, gap_seconds, radius_ratio) != "vanishing":
            continue
        tally[_kind_of_absorption(data, life, growth)] += 1
    return AbsorptionSplit(**tally)
