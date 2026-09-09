"""Отладочное видео: боксы, линия двери, полоса срабатывания, счётчики.

Отдельный проход по видео поверх кэша треков — детектор здесь не нужен.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from .core.trackdata import TrackData
from .core.types import Direction, Point, RunResult, VideoConfig
from .core.video import frames

GREEN = (80, 220, 80)
RED = (60, 60, 235)
BLUE = (235, 170, 60)
YELLOW = (60, 220, 240)
GREY = (170, 170, 170)
WHITE = (245, 245, 245)


def render_debug(
    video: Path,
    data: TrackData,
    config: VideoConfig,
    result: RunResult,
    out_path: Path,
    boxes_by_frame: dict[int, np.ndarray],
) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(out_path), cv2.VideoWriter_fourcc(*"mp4v"),
        data.effective_fps, (data.width, data.height),
    )
    by_frame = {f.frame_idx: f for f in data.frames}
    events_by_frame: dict[int, list] = {}
    for e in result.events:
        events_by_frame.setdefault(e.frame_idx, []).append(e)

    windows = [(v.arrival_ts, v.departure_ts, v.visit_id) for v in result.visits]
    boarded = alighted = 0
    flash: dict[str, int] = {}

    try:
        for frame_idx, ts, frame in frames(video, stride=data.stride):
            f = by_frame.get(frame_idx)
            if f is None:
                continue
            for e in events_by_frame.get(frame_idx, []):
                if e.direction is Direction.IN:
                    boarded += 1
                    flash["in"] = 12
                else:
                    alighted += 1
                    flash["out"] = 12

            visit_id = next((v for a, b, v in windows if a <= ts <= b), None)
            bbox = boxes_by_frame.get(frame_idx)
            lines = []
            # Зоны дверей рисуем только внутри визита: вне визита bbox не
            # стабилизирован build_scene (см. Scene) и дёргается по сырым
            # кадрам — рисование зон там выглядело бы как баг, а не как
            # честное "здесь мы не считаем".
            if bbox is not None and visit_id is not None:
                height = float(bbox[3] - bbox[1])
                for spec in config.doors:
                    a, b = spec.resolve(tuple(float(v) for v in bbox))
                    lines.append((a, b, max(spec.band * height, 1.0)))

            writer.write(
                _draw(frame, f.person_boxes, f.person_ids, bbox, lines,
                      boarded, alighted, ts, visit_id, flash)
            )
            for k in list(flash):
                flash[k] -= 1
                if flash[k] <= 0:
                    del flash[k]
    finally:
        writer.release()
    return out_path


def _draw(frame, person_boxes, person_ids, vehicle_box, lines,
          boarded, alighted, ts, visit_id, flash):
    out = frame.copy()
    h, w = out.shape[:2]
    scale = max(0.5, w / 1600)

    if vehicle_box is not None:
        x1, y1, x2, y2 = np.asarray(vehicle_box).astype(int)
        cv2.rectangle(out, (x1, y1), (x2, y2), BLUE, 2)

    for a, b, band in lines:
        pa, pb = (int(a.x), int(a.y)), (int(b.x), int(b.y))
        if band > 1:
            overlay = out.copy()
            off = int(band)
            poly = np.array(
                [[pa[0], pa[1] - off], [pb[0], pb[1] - off],
                 [pb[0], pb[1] + off], [pa[0], pa[1] + off]], dtype=np.int32
            )
            cv2.fillPoly(overlay, [poly], YELLOW)
            cv2.addWeighted(overlay, 0.18, out, 0.82, 0, out)
        cv2.line(out, pa, pb, YELLOW, 3)

    for box, tid in zip(person_boxes, person_ids):
        x1, y1, x2, y2 = np.asarray(box).astype(int)
        cv2.rectangle(out, (x1, y1), (x2, y2), GREEN, 2)
        cv2.circle(out, ((x1 + x2) // 2, y2), 4, RED, -1)
        cv2.putText(out, f"#{int(tid)}", (x1, max(14, y1 - 6)),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.5 * scale, GREEN, 1)

    bar = int(46 * scale)
    cv2.rectangle(out, (0, 0), (w, bar), (25, 25, 25), -1)
    in_color = (120, 255, 120) if "in" in flash else WHITE
    out_color = (120, 200, 255) if "out" in flash else WHITE
    cv2.putText(out, f"t={ts:6.2f}s", (10, int(bar * 0.7)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, WHITE, 2)
    cv2.putText(out, f"IN {boarded}", (int(180 * scale), int(bar * 0.7)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, in_color, 2)
    cv2.putText(out, f"OUT {alighted}", (int(300 * scale), int(bar * 0.7)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale, out_color, 2)
    label = f"визит {visit_id}" if visit_id else "вне визита"
    cv2.putText(out, label, (w - int(220 * scale), int(bar * 0.7)),
                cv2.FONT_HERSHEY_SIMPLEX, 0.7 * scale,
                GREEN if visit_id else GREY, 2)
    return out
