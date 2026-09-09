"""Метод B: геометрический поиск дверных проёмов по пикселям.

Первая версия пыталась искать проём по абсолютной темноте (порог по V в HSV).
На реальных кадрах это не сработало: борт под пасмурным небом сам довольно
тёмный и малонасыщенный, поэтому порог по яркости либо гасил весь борт, либо
не гасил ничего — окна, тень под кузовом и дверь неотличимы по одной лишь
светлоте (см. отладочные маски, приложенные к разбору задачи).

Рабочий признак — не темнота, а **непохожесть на цвет борта**: крашеный
корпус — сплошной пятно одного цвета, а дверь (тёмное стекло) и окна режут
это пятно. Дистанция считается в Lab (перцептивно ровнее HSV, не путается в
циркулярности hue), а порог берётся адаптивно с самого кадра — как процентиль
этой дистанции в опорной полосе борта — вместо одной фиксированной константы
на все условия освещения.

Дверь среди «непохожих на борт» участков отделяется тем же признаком, что и
раньше: проём **достаёт до низа корпуса**, а окно обрывается на подоконной
линии.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..core.trackdata import TrackData
from . import DoorCandidate

RESIZE_W, RESIZE_H = 256, 128
PANEL_Y0, PANEL_Y1 = 0.45, 0.85  # полоса борта: ниже окон, выше колёс
LAB_WEIGHTS = np.array([0.5, 1.0, 1.0])  # L недооцениваем — освещение гуляет больше цвета
PANEL_PERCENTILE = 90  # борт занимает бо́льшую часть полосы — это его типичный разброс
THRESHOLD_MARGIN = 5.0
BOTTOM_ROW_FRAC = 0.82  # от этой строки идём вверх, ища проём
TOP_ROW_FRAC = 0.50  # проём обязан дотянуться минимум досюда, иначе это окно
# Порог по score — тоже адаптивный, не фиксированное число. На реальных
# кадрах (блики, компрессия) score самой двери редко превышает 0.35-0.4 —
# фиксированный порог 0.5 из первой версии не пропускал вообще ничего.
# Дверь всё равно выделяется на фоне ровного борта, поэтому берём процентиль
# по самому кадру, с полом — чтобы кадр без двери в кадре не выдумывал её из шума.
SCORE_PERCENTILE = 92
SCORE_FLOOR = 0.15
CLOSE_KERNEL_FRAC = 0.02
MIN_SEGMENT_FRAC = 0.04
MAX_SEGMENT_FRAC = 0.22
# Крайние 5% ширины bbox глушим ещё до постфильтра geometry.EDGE_MARGIN:
# детектор ТС сам не идеально точен по краю бокса, и туда же может попасть
# фон из-за неточного бокса — оба случая дают ложные «непохожие на борт»
# столбцы, не имеющие отношения к двери.
SEARCH_MARGIN_FRAC = 0.05


def _body_color(lab: np.ndarray) -> np.ndarray:
    """Медиана медиан по столбцам опорной полосы — устойчива, пока дверь и
    окна вместе занимают меньше половины ширины полосы."""
    y0 = int(PANEL_Y0 * RESIZE_H)
    y1 = int(PANEL_Y1 * RESIZE_H)
    strip = lab[y0:y1, :, :]
    if strip.size == 0:
        strip = lab
    col_medians = np.median(strip, axis=0)
    return np.median(col_medians, axis=0)


def _non_body_mask(lab: np.ndarray) -> np.ndarray:
    body = _body_color(lab)
    dist = np.sqrt((((lab - body) * LAB_WEIGHTS) ** 2).sum(axis=2))
    y0 = int(PANEL_Y0 * RESIZE_H)
    y1 = int(PANEL_Y1 * RESIZE_H)
    panel_dist = dist[y0:y1, :] if y1 > y0 else dist
    threshold = float(np.percentile(panel_dist, PANEL_PERCENTILE)) + THRESHOLD_MARGIN
    return dist > threshold


REACH_DENSITY = 0.30  # доля непохожих на борт пикселей в полосе [верх, низ] — не сплошной пробег


def _column_features(non_body: np.ndarray, edges: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Для каждого столбца: плотность «непохожих на борт» пикселей в полосе
    между верхней и нижней опорной строкой, достаёт ли она до верха, и сила
    вертикальных перепадов яркости там же.

    Плотность берём вместо требования сплошного пробега без единого разрыва:
    блик на стекле или полоса отражения на реальном кадре рвёт пиксельный
    пробег там, где визуально дверь продолжается — строгий «без единого
    разрыва» тест на живом видео не переживает такой шум.
    """
    bottom_row = int(BOTTOM_ROW_FRAC * RESIZE_H)
    top_row = int(TOP_ROW_FRAC * RESIZE_H)
    band = non_body[top_row:bottom_row + 1, :]
    density = band.mean(axis=0)
    reaches = density >= REACH_DENSITY
    edge_max = float(edges.max()) or 1.0
    edge_band = edges[top_row:bottom_row + 1, :]
    edge_norm = edge_band.mean(axis=0) / edge_max
    return reaches, density, edge_norm


def _score_columns(non_body: np.ndarray, v_channel: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    sobel = cv2.Sobel(v_channel, cv2.CV_32F, 1, 0, ksize=3)
    edges = np.abs(sobel)
    reaches, density, edge_norm = _column_features(non_body, edges)
    score = 0.6 * density + 0.4 * edge_norm
    score[~reaches] = 0.0
    margin = max(1, int(round(SEARCH_MARGIN_FRAC * RESIZE_W)))
    score[:margin] = 0.0
    score[-margin:] = 0.0
    top_row = int(TOP_ROW_FRAC * RESIZE_H)
    top_rows = np.full(RESIZE_W, float(RESIZE_H))
    top_rows[reaches] = float(top_row)
    return score, top_rows


def _find_segments(score: np.ndarray) -> list[tuple[int, int]]:
    """Столбцы выше порога, с закрытием коротких разрывов (1D-closing)."""
    threshold = max(SCORE_FLOOR, float(np.percentile(score, SCORE_PERCENTILE)))
    above = score > threshold
    kernel = max(1, int(round(CLOSE_KERNEL_FRAC * RESIZE_W)))
    window = 2 * kernel + 1
    padded = np.pad(above.astype(np.uint8), (kernel, kernel))
    dilated = np.convolve(padded, np.ones(window, dtype=int), mode="same") > 0
    eroded = np.convolve(dilated.astype(int), np.ones(window, dtype=int), mode="same") >= window
    closed = eroded[kernel:-kernel]

    segments: list[tuple[int, int]] = []
    start: int | None = None
    for i, v in enumerate(closed):
        if v and start is None:
            start = i
        elif not v and start is not None:
            segments.append((start, i))
            start = None
    if start is not None:
        segments.append((start, len(closed)))

    result: list[tuple[int, int]] = []
    for a, b in segments:
        width = b - a
        if width < MIN_SEGMENT_FRAC * RESIZE_W:
            continue
        if width > MAX_SEGMENT_FRAC * RESIZE_W:
            # Слишком широко для одной двери — режем по локальному минимуму score.
            window = score[a:b]
            cut = a + int(np.argmin(window[1:-1])) + 1 if b - a > 2 else (a + b) // 2
            result.append((a, cut))
            result.append((cut, b))
        else:
            result.append((a, b))
    return result


def _frame_candidates(
    frame: np.ndarray, bbox: tuple[float, float, float, float], frame_idx: int
) -> list[DoorCandidate]:
    x1, y1, x2, y2 = (int(round(v)) for v in bbox)
    x1, y1 = max(x1, 0), max(y1, 0)
    x2, y2 = min(x2, frame.shape[1]), min(y2, frame.shape[0])
    if x2 - x1 < 10 or y2 - y1 < 10:
        return []
    crop = cv2.resize(frame[y1:y2, x1:x2], (RESIZE_W, RESIZE_H))
    crop = cv2.GaussianBlur(crop, (5, 5), 0)
    lab = cv2.cvtColor(crop, cv2.COLOR_BGR2LAB).astype(np.float32)
    non_body = _non_body_mask(lab)
    # Края считаем по L (светлота) — для рамки двери этого достаточно, а
    # пересчитывать в HSV ради одного канала незачем.
    score, top_rows = _score_columns(non_body, lab[:, :, 0])
    candidates = []
    for a, b in _find_segments(score):
        seg_score = float(score[a:b].mean())
        y0 = float(np.median(top_rows[a:b])) / RESIZE_H
        candidates.append(
            DoorCandidate(
                x0=a / RESIZE_W, y0=max(0.0, min(y0, 1.0)),
                x1=b / RESIZE_W, y1=1.0, score=seg_score, method="pixels",
                frame_idx=frame_idx,
            )
        )
    return candidates


class PixelDoorProposer:
    method_tag = "pixels"

    def propose(
        self,
        video: Path,
        data: TrackData,
        frame_indices: list[int],
        boxes_by_frame: dict[int, np.ndarray],
    ) -> list[DoorCandidate]:
        cap = cv2.VideoCapture(str(video))
        if not cap.isOpened():
            raise RuntimeError(f"не открывается видео: {video}")
        candidates: list[DoorCandidate] = []
        try:
            for idx in frame_indices:
                bbox = boxes_by_frame.get(idx)
                if bbox is None:
                    continue
                cap.set(cv2.CAP_PROP_POS_FRAMES, idx)
                ok, frame = cap.read()
                if not ok:
                    continue
                candidates.extend(
                    _frame_candidates(frame, tuple(float(v) for v in bbox), idx)
                )
        finally:
            cap.release()
        return candidates
