"""Готовое решение: supervision.LineZone «из коробки».

Ровно тот сценарий, что предлагает документация Roboflow: детектор, трекер и
линия в пикселях кадра. Ни визитов, ни привязки к транспорту. Это baseline,
относительно которого мы решаем, нужен ли свой пайплайн вообще.
"""

from __future__ import annotations

import time

import numpy as np
import supervision as sv

from ..core.trackdata import TrackData
from ..core.types import Direction, PersonEvent, Point, RunResult, VehicleVisit, VideoConfig


class SupervisionLineBackend:
    name = "sv_linezone"

    def run(self, data: TrackData, config: VideoConfig) -> RunResult:
        t0 = time.perf_counter()
        a, b = line_for(config, data.width, data.height)
        zone = sv.LineZone(
            start=sv.Point(a.x, a.y),
            end=sv.Point(b.x, b.y),
            triggering_anchors=(sv.Position.BOTTOM_CENTER,),
        )
        events: list[PersonEvent] = []

        for f in data.frames:
            if len(f.person_ids) == 0:
                continue
            detections = sv.Detections(
                xyxy=f.person_boxes.astype(np.float32),
                confidence=f.person_conf.astype(np.float32),
                class_id=np.zeros(len(f.person_ids), dtype=int),
                tracker_id=f.person_ids.astype(int),
            )
            crossed_in, crossed_out = zone.trigger(detections)
            for i, tid in enumerate(detections.tracker_id):
                if crossed_in[i]:
                    events.append(_event(data.video, f, int(tid), Direction.IN))
                elif crossed_out[i]:
                    events.append(_event(data.video, f, int(tid), Direction.OUT))

        visit = VehicleVisit(
            video=data.video, visit_id=1, vehicle_track_id=-1, arrival_ts=0.0,
            departure_ts=round(data.duration_s, 2), backend=self.name,
            boarded=int(zone.in_count), alighted=int(zone.out_count),
        )
        return RunResult(
            video=data.video, backend=self.name, duration_s=data.duration_s,
            fps=data.fps, frames_processed=len(data.frames),
            wall_seconds=time.perf_counter() - t0, visits=[visit], events=events,
        )


def line_for(config: VideoConfig, width: int, height: int) -> tuple[Point, Point]:
    """Абсолютная линия из конфига, иначе горизонталь на 60 % высоты кадра."""
    for door in config.doors:
        if door.frame == "absolute":
            return door.line_start, door.line_end
    y = height * 0.6
    return Point(x=0.0, y=y), Point(x=float(width), y=y)


def _event(video: str, f, tid: int, d: Direction) -> PersonEvent:
    return PersonEvent(
        video=video, visit_id=1, event_ts=round(f.ts, 2), frame_idx=f.frame_idx,
        person_track_id=tid, door_id="line", direction=d,
    )
