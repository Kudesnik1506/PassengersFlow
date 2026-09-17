"""Счёт людей по дверям каждого варианта: пиксели переводятся в пассажиров.

    uv run python tools/bench_count.py

Пять вариантов дверей на одних и тех же визитах, один и тот же боевой счётчик:

* **эталон** — ручная разметка. Это потолок: лучше дверей не будет. Если и он
  ошибается, выбирать между способами поиска дверей незачем — узкое место не
  там;
* **groundingdino**, **vlpart**, **owlv2** — по K дверей, отобранных по
  собственной уверенности модели;
* **совет** — голосование этих трёх ответов.

Детекция людей идёт только по окнам стоянок (`bench/tracks.py`), окно стоянки
определяется совпадением рамки ТС с размеченным кузовом, а не назначается
вокруг размеченного кадра.

Наружу уходят только числа (решение 018): кадры не сохраняются.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import case_from_layout  # noqa: E402
from paxcount.bench.counting import (  # noqa: E402
    DETECT_WINDOW_S, DOOR_SIDE_MARGIN, VARIANTS, count_doors, door_id,
    stop_window, variants_for,
)
from paxcount.bench.summary import load_runs, match_row  # noqa: E402
from paxcount.bench.tracks import tracks_in_windows  # noqa: E402
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.core.video import probe  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR, detector_for, videos_in  # noqa: E402
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount import truth_counts, truth_rows  # noqa: E402
from paxcount.settings import TARGET_ERROR  # noqa: E402

console = Console(width=190)

WINDOW_S = DETECT_WINDOW_S


def _video_path(name: str) -> Path | None:
    return next((p for p in videos_in(None) if p.stem == name), None)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doors", type=Path, default=DATA_DIR / "truth" / "doors")
    parser.add_argument("--bench", type=Path, default=OUT_DIR / "bench_doors.json")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    parser.add_argument("--side-margin", type=float, default=None,
                        help="боковой допуск дверной зоны, долями высоты полосы ног")
    parser.add_argument("--band-from-sill", type=float, default=None,
                        help="поднять верх полосы до порога двери, долями высоты проёма")
    parser.add_argument("--per-door-band", action="store_true",
                        help="отсчитывать полосу ног от порога каждой двери")
    parser.add_argument("--refresh", action="store_true",
                        help="пересчитать детекцию, не брать кэш окон")
    args = parser.parse_args()

    rows = truth_rows.load(DATA_DIR / "truth" / "rows.csv")
    counts = truth_counts.load(DATA_DIR / "truth" / "door_counts.csv")
    by_door = {(c.visit, c.door): c for c in counts}
    truth_totals = truth_counts.totals(counts)
    runs = load_runs(args.bench)

    layouts, cases = {}, {}
    for path in sorted(args.doors.rglob("*.json")):
        layout = load_door_layout(path)
        if layout.camera not in COUNTING_CAMERAS:
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            console.print(f"[yellow]пропущено:[/yellow] {problem}")
            continue
        layouts[case.visit_key] = layout
        cases[case.visit_key] = case

    # Детекция: по одному проходу на файл, окна внутри него — кэшируются.
    tracks = {}
    by_video: dict[str, dict[str, tuple[int, int]]] = {}
    for key, case in cases.items():
        by_video.setdefault(case.video, {})[key] = case.frame_idx
    for video_name, marks in by_video.items():
        path = _video_path(video_name)
        if path is None:
            console.print(f"[yellow]нет файла {video_name}[/yellow]")
            continue
        fps = probe(path).fps or 30.0
        half = int(round(WINDOW_S * fps))
        windows = {k: (max(0, f - half), f + half) for k, f in marks.items()}
        console.print(f"детекция людей: {video_name}, окон {len(windows)}, "
                       f"по {2 * WINDOW_S:.0f} с, шаг {detector_for(path).stride}")
        tracks.update(tracks_in_windows(path, windows, refresh=args.refresh,
                                         progress=_progress))

    visits, doors, scored = [], [], []
    for key, case in sorted(cases.items()):
        row = match_row(case.camera, visit_moment(key), rows)
        number = row.number if row else key
        data = tracks.get(key)
        if data is None:
            console.print(f"[yellow]визит {number}: нет треков[/yellow]")
            continue
        window = stop_window(data, case.body_px)
        people = len({int(i) for f in data.frames for i in f.person_ids})
        if window is None:
            console.print(f"[yellow]визит {number}: рамка ТС ни на одном кадре не "
                           "совпала с размеченным кузовом — окна счёта нет[/yellow]")
            visits.append({"№": number, "окно, с": "", "длительность, с": "",
                            "треков людей": people,
                            "эталон": _truth(truth_totals.get(str(number))),
                            **{v: "" for v in VARIANTS}})
            continue

        per_variant = {}
        for variant, layout in variants_for(key, case, layouts[key], runs).items():
            if layout is None:
                per_variant[variant] = None
                continue
            margin = (DOOR_SIDE_MARGIN if args.side_margin is None
                       else args.side_margin)
            per_variant[variant] = count_doors(
                data, layout, window, margin, args.per_door_band,
                args.band_from_sill)

        visits.append({
            "№": number,
            "окно, с": f"{window[0]:.1f}-{window[1]:.1f}",
            "длительность, с": f"{window[1] - window[0]:.1f}",
            "треков людей": people,
            "эталон": _truth(truth_totals.get(str(number))),
            **{v: _total(per_variant.get(v)) for v in VARIANTS},
        })
        for door in layouts[key].doors:
            measured = by_door.get((str(number), door.n_from_nose))
            doors.append({
                "№": number, "дверь": door.n_from_nose,
                "в кадре": "да" if door.in_frame else "нет",
                "эталон": _truth((measured.boarded, measured.alighted)
                                  if measured else None),
                **{v: _cell(per_variant.get(v), door_id(door.n_from_nose))
                    for v in VARIANTS},
            })
        scored.append((str(number), truth_totals.get(str(number)), per_variant))

    _print(visits, doors)
    _accuracy(scored)
    args.out.mkdir(parents=True, exist_ok=True)
    _csv(args.out / "bench_count_visits.csv", visits)
    _csv(args.out / "bench_count_doors.csv", doors)
    console.print(f"\nтаблицы: {args.out / 'bench_count_visits.csv'}, "
                   f"{args.out / 'bench_count_doors.csv'}")
    return 0


def _progress(key: str, idx: int, last: int) -> None:
    if idx % 900 == 0:
        console.print(f"  кадр {idx} из {last}", highlight=False)


def _truth(pair) -> str:
    """Эталонная пара «вошло/вышло». `N/A` — не считали, а не ноль."""
    if pair is None:
        return ""
    inn, out = pair
    na = truth_counts.NA
    return f"{na if inn is None else inn}/{na if out is None else out}"


def _sum(counts, index: int) -> int:
    return sum(pair[index] for pair in counts.values())


def _accuracy(scored) -> None:
    """Ошибка каждого варианта против эталона — по направлениям отдельно.

    По направлениям, а не одним числом: недосчёт входов и перебор выходов
    гасят друг друга в сумме, и вариант, ошибшийся дважды, выглядел бы точным
    (решение 013). Визиты, где эталон не измерен, в знаменатель не идут.
    """
    table = Table(title=f"Ошибка против эталона (планка {TARGET_ERROR:.0%})")
    for column in ("вариант", "вошло", "эталон вошло", "ошибка",
                    "вышло", "эталон вышло", "ошибка"):
        table.add_column(column, no_wrap=True)
    for variant in VARIANTS:
        cells = [variant]
        for index in (0, 1):
            mine = truth = 0
            for _number, pair, per_variant in scored:
                counts = per_variant.get(variant)
                if pair is None or pair[index] is None or counts is None:
                    continue
                truth += pair[index]
                mine += _sum(counts, index)
            if truth == 0:
                cells += [str(mine), "—", "—"]
                continue
            error = abs(mine - truth) / truth
            mark = "[green]" if error <= TARGET_ERROR else "[red]"
            cells += [str(mine), str(truth), f"{mark}{error:.0%}[/]"]
        table.add_row(*cells)
    console.print(table)


def _total(counts) -> str:
    if counts is None:
        return ""
    inn = sum(i for i, _ in counts.values())
    out = sum(o for _, o in counts.values())
    return f"{inn}/{out}"


def _cell(counts, door: str) -> str:
    if counts is None:
        return "—"
    if door not in counts:
        return "·"          # у этого варианта такой двери нет
    inn, out = counts[door]
    return f"{inn}/{out}"


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print(visits, doors) -> None:
    table = Table(title="Визиты: вошло/вышло по вариантам дверей")
    for column in ("№", "окно, с", "длительность, с", "треков людей",
                    "эталон", *VARIANTS):
        table.add_column(column, no_wrap=True)
    for v in visits:
        table.add_row(str(v["№"]), v["окно, с"], v["длительность, с"],
                       str(v["треков людей"]), v["эталон"],
                       *[str(v[k]) for k in VARIANTS])
    console.print(table)
    console.print("[dim]вошло/вышло; пусто — окно стоянки не определено, "
                   "«—» вариант не построен[/dim]")

    detail = Table(title="Двери: вошло/вышло («·» — у варианта такой двери нет)")
    for column in ("№", "дверь", "в кадре", "эталон", *VARIANTS):
        detail.add_column(column, no_wrap=True)
    for d in doors:
        detail.add_row(str(d["№"]), str(d["дверь"]), d["в кадре"], d["эталон"],
                        *[str(d[k]) for k in VARIANTS])
    console.print(detail)


if __name__ == "__main__":
    raise SystemExit(main())
