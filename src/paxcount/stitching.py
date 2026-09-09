"""Сшивка разорванных треков людей — до счёта, а не вместо него.

Замер на видео 04: 50 из 102 треков людей короче 8 кадров (~0.5 с), а три
трека прямо в дверном проёме (10-14 кадров) не пересекают линию вообще —
трекер их обрывает в толпе, и следующий фрагмент того же человека рождается
уже с другой стороны линии как «новый» трек, никогда не дав события.

Гейт для сшивки — тот же, что раньше использовался только для подавления
сомнительных событий (``zonecount._is_id_switch``): смерть одного трека
рядом по времени и месту с рождением другого — обычно перекрытие одного и
того же человека, а не совпадение двух разных. Раньше на этом сигнале
событие терялось; здесь тот же сигнал сшивает id **до** счёта, и событие
досчитывается по объединённому треку.

Работает на сырых треках людей, не привязана к дверям и визитам — поэтому
применяется один раз при загрузке кэша (``tracking.get_tracks``), а не
внутри каждого бэкенда отдельно. Сам кэш детекции не трогается: сшивка
дешёвая (миллисекунды) и пересчитывается каждый раз, чтобы смена порогов
не читалась как невалидный старый кэш.
"""

from __future__ import annotations

import numpy as np

from .core.trackdata import TrackData

GAP_SECONDS = 0.3
RADIUS_RATIO = 0.5


def _anchor(box: np.ndarray) -> tuple[float, float]:
    return float((box[0] + box[2]) / 2), float(box[3])


def _track_endpoints(
    data: TrackData,
) -> tuple[dict[int, tuple[float, np.ndarray]], dict[int, tuple[float, np.ndarray]]]:
    first: dict[int, tuple[float, np.ndarray]] = {}
    last: dict[int, tuple[float, np.ndarray]] = {}
    for f in data.frames:
        for tid, box in zip(f.person_ids, f.person_boxes):
            tid = int(tid)
            if tid not in first:
                first[tid] = (f.ts, box)
            last[tid] = (f.ts, box)
    return first, last


def build_remap(
    data: TrackData, gap_seconds: float = GAP_SECONDS, radius_ratio: float = RADIUS_RATIO
) -> dict[int, int]:
    """id разорванного фрагмента -> id первого фрагмента той же цепочки.

    Жадное сопоставление по возрастанию зазора: лучшая пара забирает id
    первой. Цикл невозможен по построению — ребро идёт только вперёд по
    времени (рождение строго позже смерти), значит вернуться в прошлое
    сшивка не может.
    """
    first, last = _track_endpoints(data)
    ids = sorted(first)
    edges: list[tuple[float, int, int]] = []
    for a in ids:
        a_ts, a_box = last[a]
        a_anchor = _anchor(a_box)
        a_h = max(float(a_box[3] - a_box[1]), 1.0)
        for b in ids:
            if a == b:
                continue
            b_ts, b_box = first[b]
            gap = b_ts - a_ts
            if not (0 < gap <= gap_seconds):
                continue
            b_anchor = _anchor(b_box)
            dist = float(np.hypot(b_anchor[0] - a_anchor[0], b_anchor[1] - a_anchor[1]))
            if dist <= radius_ratio * a_h:
                edges.append((gap, a, b))
    edges.sort(key=lambda e: e[0])

    used_death: set[int] = set()
    used_birth: set[int] = set()
    next_of: dict[int, int] = {}
    for _, a, b in edges:
        if a in used_death or b in used_birth:
            continue
        used_death.add(a)
        used_birth.add(b)
        next_of[a] = b

    remap: dict[int, int] = {}
    for tid in ids:
        if tid in used_birth:
            continue  # не корень цепочки — попадёт в remap через свой корень
        cur = tid
        remap[cur] = tid
        while cur in next_of:
            cur = next_of[cur]
            remap[cur] = tid
    return remap


def stitch_person_tracks(
    data: TrackData, gap_seconds: float = GAP_SECONDS, radius_ratio: float = RADIUS_RATIO
) -> TrackData:
    """Переприсваивает id людей по цепочкам сшивки. Мутирует и возвращает data."""
    remap = build_remap(data, gap_seconds, radius_ratio)
    if not any(k != v for k, v in remap.items()):
        return data
    for f in data.frames:
        if len(f.person_ids) == 0:
            continue
        f.person_ids = np.array(
            [remap.get(int(t), int(t)) for t in f.person_ids], dtype=f.person_ids.dtype
        )
    return data
