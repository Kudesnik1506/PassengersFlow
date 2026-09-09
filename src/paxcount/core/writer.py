"""Запись результатов в csv. Схема одна для всех бэкендов."""

from __future__ import annotations

import csv
import json
from pathlib import Path

from .types import RunResult

VISIT_FIELDS = [
    "video", "visit_id", "vehicle_track_id", "arrival_ts", "departure_ts",
    "boarded", "alighted", "backend", "confidence",
]
EVENT_FIELDS = [
    "video", "visit_id", "event_ts", "frame_idx", "person_track_id",
    "door_id", "direction", "confidence",
]


def write_run(result: RunResult, out_dir: Path) -> dict[str, Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    visits_path = out_dir / "visits.csv"
    events_path = out_dir / "events.csv"
    run_path = out_dir / "run.json"

    with visits_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=VISIT_FIELDS)
        w.writeheader()
        for v in result.visits:
            w.writerow({k: getattr(v, k) for k in VISIT_FIELDS})

    with events_path.open("w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=EVENT_FIELDS)
        w.writeheader()
        for e in result.events:
            row = {k: getattr(e, k) for k in EVENT_FIELDS}
            row["direction"] = e.direction.value
            w.writerow(row)

    run_path.write_text(
        json.dumps(
            {
                "video": result.video,
                "backend": result.backend,
                "duration_s": round(result.duration_s, 2),
                "fps": round(result.fps, 2),
                "frames_processed": result.frames_processed,
                "wall_seconds": round(result.wall_seconds, 2),
                "visits": len(result.visits),
                "boarded": result.boarded,
                "alighted": result.alighted,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return {"visits": visits_path, "events": events_path, "run": run_path}
