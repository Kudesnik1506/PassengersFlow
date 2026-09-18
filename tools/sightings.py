"""Стоянки, найденные детекцией: когда, какой рамкой, в каком кадре.

    uv run python tools/sightings.py out/detect-k2/<прогон>/custom 2 22739

Таблица нужна сборке книги: по ней ставится код таблицы 2 там, где ручной
разметки нет (решение 081). Логика — в `delivery.visibility` и
`delivery.reconcile`, здесь только доступ к кэшу треков и к файлам прогона.

Рамка берётся каноническая по окну стоянки, а не покадровая (запрет 3): у
покадровой край кадра то задевается, то нет, и код бы мигал от кадра к кадру.
"""

from __future__ import annotations

import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402

from paxcount.delivery.timeline import parse_slot  # noqa: E402
from paxcount.settings import OUT_DIR, detector_for, videos_in  # noqa: E402
from paxcount.tracking import get_tracks  # noqa: E402
from paxcount.visits import canonical_box, vehicle_tracks  # noqa: E402

console = Console()
FIELDS = ("camera", "file", "visit_id", "track", "start", "end",
           "x0", "y0", "x1", "y1", "width", "height")


def main() -> int:
    if len(sys.argv) != 4:
        console.print("нужно: <папка прогона> <камера> <остановка>")
        return 2
    runs, camera, stop = Path(sys.argv[1]), sys.argv[2], sys.argv[3]
    videos = {p.stem: p for p in videos_in(None)}

    out: list[dict] = []
    folders = sorted(p for p in runs.iterdir() if p.is_dir())
    for n, folder in enumerate(folders, start=1):
        rows = list(csv.DictReader((folder / "visits.csv").open(encoding="utf-8")))
        video = videos.get(folder.name)
        if video is None:
            console.print(f"[yellow]нет файла {folder.name}[/yellow]")
            continue
        slot = parse_slot(folder.name)
        if slot.stop != stop:
            continue
        data, _, _ = get_tracks(video, detector_for(video))
        tracks = vehicle_tracks(data)
        for row in rows:
            track = tracks.get(int(row["vehicle_track_id"]))
            if track is None:
                continue
            arrival, departure = float(row["arrival_ts"]), float(row["departure_ts"])
            seen = [b for t, b in zip(track.times, track.boxes)
                    if arrival <= t <= departure]
            box = canonical_box(seen)
            if box is None:
                # Наблюдений меньше порога — стабилизировать нечего, а сырая
                # рамка у края кадра врёт в обе стороны (`visits.canonical_box`).
                continue
            out.append({
                "camera": camera, "file": folder.name, "visit_id": row["visit_id"],
                "track": row["vehicle_track_id"],
                "start": slot.at(arrival).isoformat(),
                "end": slot.at(departure).isoformat(),
                "x0": round(float(box[0]), 1), "y0": round(float(box[1]), 1),
                "x1": round(float(box[2]), 1), "y1": round(float(box[3]), 1),
                "width": data.width, "height": data.height,
            })
        if n % 10 == 0:
            console.print(f"  {n} из {len(folders)}")

    path = OUT_DIR / "sightings" / f"{stop}-{camera}.csv"
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(FIELDS))
        writer.writeheader()
        writer.writerows(out)
    console.print(f"стоянок: {len(out)} → {path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
