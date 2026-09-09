"""Автопредложение геометрии дверей.

Два независимых метода отвечают на один вопрос — «где на корпусе ТС дверные
проёмы» — и приводятся к общему формату кандидатов, чтобы дальше пройти один
и тот же постфильтр (``filters.py``) и лечь в один и тот же кэш предложения
(``data/zones/<stem>.auto__...json``). Это специально позволяет сравнить
методы на одном видео без переписывания вызывающего кода.

Детекция дверей стоит секунды (пиксели видео + вес модели), поэтому она не
прячется внутри дешёвого ``doors.load_config()`` — вызывается явно из CLI или
кнопки веб-UI, см. решение по интеграции в плане.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

import numpy as np

from ..core.trackdata import TrackData


@dataclass(frozen=True)
class DoorCandidate:
    """Один предложенный дверной проём в долях bbox ТС."""

    x0: float
    y0: float
    x1: float
    y1: float
    score: float
    method: str
    frame_idx: int = -1  # с какого опорного кадра предложен — для агрегации по k кадрам


class DoorProposer(Protocol):
    """Общий контракт метода детекции дверей."""

    method_tag: str

    def propose(
        self,
        video: Path,
        data: TrackData,
        frame_indices: list[int],
        boxes_by_frame: dict[int, "object"],
    ) -> list[DoorCandidate]:
        """Возвращает необработанных кандидатов — до постфильтра и NMS."""
        ...


AGGREGATE_TOLERANCE = 0.05  # доля ширины bbox ТС — допуск при кластеризации по центру x
QUORUM = 0.6  # кластер должен набраться минимум с такой доли опорных кадров


def _center(c: DoorCandidate) -> float:
    return (c.x0 + c.x1) / 2


def aggregate_candidates(
    candidates: list[DoorCandidate], frame_count: int
) -> list[DoorCandidate]:
    """Сводит кандидатов с k опорных кадров к одному предложению на дверь.

    Один кадр может дать шум (блик, отражение), поэтому кандидату надо
    подтвердиться на большинстве опорных кадров — порознь на них дверь может
    сдвинуться на пиксель-два, здесь это гасится медианой.
    """
    if not candidates or frame_count <= 0:
        return []
    ordered = sorted(candidates, key=_center)
    clusters: list[list[DoorCandidate]] = []
    for cand in ordered:
        if clusters and _center(cand) - _center(clusters[-1][-1]) <= AGGREGATE_TOLERANCE:
            clusters[-1].append(cand)
        else:
            clusters.append([cand])

    result: list[DoorCandidate] = []
    quorum_n = max(1, int(round(QUORUM * frame_count)))
    for cluster in clusters:
        frames_seen = {c.frame_idx for c in cluster}
        if len(frames_seen) < quorum_n:
            continue
        result.append(
            DoorCandidate(
                x0=float(np.median([c.x0 for c in cluster])),
                y0=float(np.median([c.y0 for c in cluster])),
                x1=float(np.median([c.x1 for c in cluster])),
                y1=float(np.median([c.y1 for c in cluster])),
                score=float(np.mean([c.score for c in cluster])),
                method=cluster[0].method,
            )
        )
    return result


def propose_doors(
    proposer: DoorProposer,
    video: Path,
    data: TrackData,
    frame_indices: list[int],
    boxes_by_frame: dict,
    truncated_left: bool = False,
    truncated_right: bool = False,
) -> list[DoorCandidate]:
    """Полный шаг предложения: детекция → агрегация по кадрам → постфильтр."""
    from .filters import keep_doors

    raw = proposer.propose(video, data, frame_indices, boxes_by_frame)
    aggregated = aggregate_candidates(raw, len(frame_indices))
    return keep_doors(aggregated, truncated_left, truncated_right)
