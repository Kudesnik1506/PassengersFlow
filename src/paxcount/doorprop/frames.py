"""Выбор опорных кадров для автопредложения дверей.

Дверь ищем не по одному кадру, а по k, чтобы дрожание рамки детектора и
случайный блик не читались как проём. Кадры берём из самого длинного визита
(там ТС дольше всего стоит) и предпочитаем те, где bbox ТС крупнее и дальше
от края кадра — обрезанный корпус даёт обрезанную дверь.
"""

from __future__ import annotations

import numpy as np

from ..core.trackdata import TrackData
from ..core.types import VehicleVisit
from ..zonecount import BORDER_MARGIN

MIN_GAP_SECONDS = 0.5


def _edge_penalty(box: np.ndarray, width: int, height: int) -> float:
    """0 — bbox нигде не касается края кадра, 1 — прижат хотя бы одной стороной.

    Порог обрезки — тот же ``BORDER_MARGIN``, которым zonecount отличает
    настоящий проём двери от трека, оборвавшегося на краю кадра: если он
    считает край края кадра, то и здесь он должен считаться тем же краем.
    """
    margins = [
        box[0], box[1], width - box[2], height - box[3],
    ]
    closest = max(0.0, min(margins))
    return 1.0 - min(1.0, closest / max(BORDER_MARGIN, 1))


def pick_proposal_frames(
    data: TrackData,
    boxes_by_frame: dict[int, np.ndarray],
    visit: VehicleVisit,
    k: int = 9,
) -> list[int]:
    """k кадров визита с наибольшим и наименее обрезанным bbox ТС.

    Кадры разнесены минимум на ``MIN_GAP_SECONDS``, чтобы не набрать k почти
    одинаковых соседних кадров одного и того же момента.
    """
    frame_area = float(data.width * data.height) or 1.0
    scored: list[tuple[float, int]] = []
    for f in data.frames:
        if not (visit.arrival_ts <= f.ts <= visit.departure_ts):
            continue
        box = boxes_by_frame.get(f.frame_idx)
        if box is None:
            continue
        area = float((box[2] - box[0]) * (box[3] - box[1])) / frame_area
        penalty = _edge_penalty(box, data.width, data.height)
        score = area * (1.0 - penalty)
        scored.append((score, f.frame_idx))

    # Больший score раньше; при равенстве — меньший frame_idx, для детерминизма.
    scored.sort(key=lambda pair: (-pair[0], pair[1]))

    chosen: list[int] = []
    chosen_ts: list[float] = []
    ts_by_frame = {f.frame_idx: f.ts for f in data.frames}
    for score, idx in scored:
        if len(chosen) >= k:
            break
        ts = ts_by_frame[idx]
        if any(abs(ts - t) < MIN_GAP_SECONDS for t in chosen_ts):
            continue
        chosen.append(idx)
        chosen_ts.append(ts)

    # Мало кандидатов после разноса по времени — добираем без ограничения на
    # интервал, лучше повторить похожий кадр, чем остаться с пустым списком.
    if len(chosen) < min(k, len(scored)):
        for score, idx in scored:
            if len(chosen) >= k:
                break
            if idx not in chosen:
                chosen.append(idx)

    return sorted(chosen)
