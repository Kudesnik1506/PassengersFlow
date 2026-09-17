"""Почему трек не стал событием: разбор счёта по эталону, визит за визитом.

    uv run python tools/count_why.py            # все визиты
    uv run python tools/count_why.py -v 4       # один

Инструмент ничего не чинит и ничего не настраивает. Он отвечает на вопрос, без
которого чинить нельзя: эталон говорит «вошло семеро», счётчик насчитал троих —
на чём отвалились остальные четверо.

Причину называет сам счётчик (`zonecount.classify` возвращает её второй
величиной), поэтому здесь нет собственной копии правил: разбор показывает то,
что происходит в бою, а не то, что о нём думает разборщик.

Наружу уходят только числа (решение 018).
"""

from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import numpy as np  # noqa: E402
from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import case_from_layout  # noqa: E402
from paxcount.bench.counting import door_specs, stop_window  # noqa: E402
from paxcount.bench.summary import match_row  # noqa: E402
from paxcount.bench.tracks import tracks_in_windows  # noqa: E402
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.core.types import Direction, VehicleVisit  # noqa: E402
from paxcount.core.video import probe  # noqa: E402
from paxcount.settings import DATA_DIR, videos_in  # noqa: E402
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount.zonecount import (  # noqa: E402
    MIN_TRACK_SECONDS, activity_window, classify, collect_lives, event_moment,
    edge_guard_seconds, _touches_border,
)
from paxcount import truth_counts, truth_rows  # noqa: E402

console = Console(width=190)
WINDOW_S = 25.0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-v", "--visit", action="append", dest="visits")
    args = parser.parse_args()

    rows = truth_rows.load(DATA_DIR / "truth" / "rows.csv")
    totals = truth_counts.totals(truth_counts.load(DATA_DIR / "truth" / "door_counts.csv"))

    reasons: Counter = Counter()
    summary = []
    for path in sorted((DATA_DIR / "truth" / "doors").rglob("*.json")):
        layout = load_door_layout(path)
        if layout.camera not in COUNTING_CAMERAS:
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            continue
        row = match_row(layout.camera, visit_moment(layout.visit_key), rows)
        number = row.number if row else layout.visit_key
        if args.visits and number not in args.visits:
            continue

        video = next((p for p in videos_in(None) if p.stem == case.video), None)
        if video is None:
            console.print(f"[yellow]нет файла {case.video}[/yellow]")
            continue
        half = int(round(WINDOW_S * (probe(video).fps or 30.0)))
        key = f"v{number}"
        data = tracks_in_windows(
            video, {key: (max(0, case.frame_idx - half), case.frame_idx + half)})[key]

        window = stop_window(data, case.body_px)
        if window is None:
            console.print(f"[yellow]визит {number}: окно стоянки не определено[/yellow]")
            continue
        specs = door_specs(layout)
        # Как в счёте: рамка на всех кадрах отрезка, окно ограничивает событие.
        boxes = {f.frame_idx: np.asarray(layout.body_px, dtype=float)
                  for f in data.frames}
        visit = VehicleVisit(video=data.video, visit_id=1, vehicle_track_id=-1,
                              arrival_ts=window[0], departure_ts=window[1])
        lo, hi = activity_window(data, specs, visit, boxes)
        visit = visit.model_copy(update={"arrival_ts": lo, "departure_ts": hi})
        lives = collect_lives(data, specs, visit, boxes)

        table = Table(title=f"Визит {number}: эталон {_pair(totals.get(str(number)))}, "
                             f"окно {lo:.1f}-{hi:.1f} с")
        for column in ("трек", "жизнь, с", "кадров", "в зоне", "родился в зоне",
                        "умер в зоне", "край кадра", "событие", "в окне", "причина"):
            table.add_column(column, no_wrap=True)
        counted = Counter()
        for tid, life in sorted(lives.items()):
            if life.in_zone_frames == 0:
                continue
            direction, why = classify(life, visit, data.width, data.height,
                                       MIN_TRACK_SECONDS, edge_guard_seconds(visit))
            # То же условие и тот же момент, что в счёте: история трека берётся
            # целиком, а событие обязано попасть в окно визита. Своя копия
            # датировки здесь уже расходилась со счётом на целый визит.
            moment = (event_moment(life, direction)[0]
                       if direction is not None else life.last_ts)
            in_window = (direction is not None
                          and visit.arrival_ts <= moment <= visit.departure_ts)
            if in_window:
                counted[direction.value] += 1
            elif direction is not None:
                reasons["событие вне окна визита"] += 1
            else:
                reasons[why.split(":")[0].strip()] += 1
            edge = []
            if _touches_border(life.first_box, data.width, data.height):
                edge.append("нач")
            if _touches_border(life.last_box, data.width, data.height):
                edge.append("кон")
            table.add_row(
                str(tid), f"{life.first_ts:.1f}-{life.last_ts:.1f}", str(life.frames),
                str(life.in_zone_frames), "да" if life.first_in_zone else "нет",
                "да" if life.last_in_zone else "нет", "+".join(edge) or "—",
                direction.value if direction else "—",
                ("да" if in_window else "нет") if direction else "—", why,
            )
        console.print(table)
        summary.append((number, totals.get(str(number)), counted,
                         sum(1 for lf in lives.values() if lf.in_zone_frames)))

    _totals(summary, reasons)
    return 0


def _pair(pair) -> str:
    if pair is None:
        return ""
    inn, out = pair
    na = truth_counts.NA
    return f"{na if inn is None else inn}/{na if out is None else out}"


def _totals(summary, reasons: Counter) -> None:
    table = Table(title="Итог разбора")
    for column in ("визит", "эталон", "насчитано", "треков в дверной зоне", "потеряно"):
        table.add_column(column, no_wrap=True)
    for number, truth, counted, near in summary:
        mine = f"{counted.get('in', 0)}/{counted.get('out', 0)}"
        lost = ""
        if truth is not None:
            lost = "/".join(
                "—" if t is None else str(t - counted.get(d, 0))
                for t, d in zip(truth, ("in", "out"))
            )
        table.add_row(str(number), _pair(truth), mine, str(near), lost)
    console.print(table)

    if reasons:
        why = Table(title="Почему трек у двери не стал событием")
        why.add_column("причина"); why.add_column("треков")
        for reason, count in reasons.most_common():
            why.add_row(reason, str(count))
        console.print(why)


if __name__ == "__main__":
    raise SystemExit(main())
