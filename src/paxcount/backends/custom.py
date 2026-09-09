"""Свой пайплайн: визиты ТС → линия двери, привязанная к ТС → события.

Работает поверх кэша треков, поэтому логику можно править и перепрогонять за
секунды. Отличия от готового решения: линия двери привязана к bbox
транспортного средства, счёт ведётся по точке опоры человека, события
засчитываются только внутри окна визита, а исчезновение трека в дверном
проёме трактуется как посадка.
"""

from __future__ import annotations

import time
from pathlib import Path

import numpy as np

from ..core.geometry import LineCounter
from ..core.trackdata import TrackData
from ..core.types import (
    Direction, DoorSpec, PersonEvent, RunResult, VehicleVisit, VideoConfig,
)
from ..visits import build_scene
from ..zonecount import activity_window, count_zone

GONE_AFTER_SECONDS = 0.4


def _aspect(boxes: dict[int, np.ndarray]) -> float | None:
    """Вытянутость рамки ТС (ширина/высота), медиана по кадрам визита.

    По ней выбирается набор дверей под тип машины — см. VideoConfig.doors_for.
    """
    if not boxes:
        return None
    arr = np.asarray(list(boxes.values()), dtype=float)
    w = arr[:, 2] - arr[:, 0]
    h = np.maximum(arr[:, 3] - arr[:, 1], 1.0)
    return float(np.median(w / h))


class CustomBackend:
    name = "custom"

    def run(self, data: TrackData, config: VideoConfig) -> RunResult:
        t0 = time.perf_counter()
        fps = data.effective_fps

        scene = build_scene(data, config)
        visits = scene.visits
        if not visits:
            # Эрзац-визит на всё видео: детект визитов ничего не нашёл (ТС не
            # обнаружено или не останавливалось). stationary=False — иначе
            # build_scene стабилизировал бы канонический bbox по движущемуся
            # ТС (например, видео 02, где автобус едет на камеру весь ролик).
            visits = [
                VehicleVisit(
                    video=data.video, visit_id=1, vehicle_track_id=-1,
                    arrival_ts=0.0, departure_ts=round(data.duration_s, 2),
                    stationary=False,
                )
            ]

        # Каждый визит считается по рамке СВОЕГО ТС: на длинной записи визитов
        # много и от разных машин, а две могут стоять одновременно — одной
        # картой кадр→bbox такую сцену не описать.
        boxes_by_visit = {v.visit_id: scene.boxes_for(v) for v in visits}
        # ...и по геометрии дверей своего типа ТС: у маршрутки одна дверь там,
        # где у автобуса борт (см. VideoConfig.doors_for).
        specs_by_visit = {
            v.visit_id: config.doors_for(_aspect(boxes_by_visit[v.visit_id]))
            for v in visits
        }

        # Счёт идёт по суженному окну активности у дверей, а не по всему
        # визиту — отчёт (visits.csv) при этом показывает исходное время
        # стоянки ТС, id визита те же, так что агрегация boarded/alighted
        # ниже не меняется.
        counting_visits = [
            v.model_copy(update=dict(zip(
                ("arrival_ts", "departure_ts"),
                activity_window(
                    data, specs_by_visit[v.visit_id], v, boxes_by_visit[v.visit_id]
                ),
            )))
            for v in visits
        ]

        events = self._count(data, config, counting_visits, boxes_by_visit, specs_by_visit, fps)
        events += count_zone(data, config, counting_visits, boxes_by_visit, specs_by_visit)
        events.sort(key=lambda e: (e.event_ts, e.person_track_id))

        for v in visits:
            v.backend = self.name
            v.boarded = sum(
                1 for e in events
                if e.visit_id == v.visit_id and e.direction is Direction.IN
            )
            v.alighted = sum(
                1 for e in events
                if e.visit_id == v.visit_id and e.direction is Direction.OUT
            )

        return RunResult(
            video=data.video, backend=self.name, duration_s=data.duration_s,
            fps=data.fps, frames_processed=len(data.frames),
            wall_seconds=time.perf_counter() - t0, visits=visits, events=events,
        )

    def _count(
        self,
        data: TrackData,
        config: VideoConfig,
        visits: list[VehicleVisit],
        boxes_by_visit: dict[int, dict[int, np.ndarray]],
        specs_by_visit: dict[int, list[DoorSpec]],
        fps: float,
    ) -> list[PersonEvent]:
        events: list[PersonEvent] = []
        gone_after = max(1, int(round(GONE_AFTER_SECONDS * fps)))

        for visit in visits:
            boxes_by_frame = boxes_by_visit[visit.visit_id]
            specs = specs_by_visit[visit.visit_id]
            counters = {
                d.door_id: LineCounter(
                    door_id=d.door_id, inside_sign=d.inside_sign,
                    hysteresis=config.hysteresis_frames,
                )
                for d in specs
            }
            seen_last: dict[int, int] = {}
            step = 0

            for f in data.frames:
                if not (visit.arrival_ts <= f.ts <= visit.departure_ts):
                    continue
                step += 1
                bbox = boxes_by_frame.get(f.frame_idx)
                if bbox is None:
                    continue
                height = float(bbox[3] - bbox[1])

                for spec in specs:
                    if spec.mode != "line":
                        continue
                    a, b = spec.resolve(tuple(float(v) for v in bbox))
                    counter = counters[spec.door_id]
                    counter.max_door_distance = max(spec.band * height, 1.0)

                    for tid, pbox in zip(f.person_ids, f.person_boxes):
                        tid = int(tid)
                        seen_last[tid] = step
                        direction = counter.update(tid, pbox, a, b, step)
                        if direction is not None:
                            events.append(
                                _event(data.video, visit.visit_id, f, tid,
                                       spec.door_id, direction)
                            )

                    for tid, last in list(seen_last.items()):
                        if step - last == gone_after:
                            direction = counter.resolve_disappearance(
                                tid, step, gone_after
                            )
                            if direction is not None:
                                events.append(
                                    _event(data.video, visit.visit_id, f, tid,
                                           spec.door_id, direction, confidence=0.7)
                                )
        events.sort(key=lambda e: (e.event_ts, e.person_track_id))
        return events


def _event(video, visit_id, f, tid, door_id, direction, confidence=1.0) -> PersonEvent:
    return PersonEvent(
        video=video, visit_id=visit_id, event_ts=round(f.ts, 2), frame_idx=f.frame_idx,
        person_track_id=tid, door_id=door_id, direction=direction, confidence=confidence,
    )
