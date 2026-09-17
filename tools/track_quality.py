"""Качество следов на окнах стоянок: рвутся они или поглощаются.

    uv run python tools/track_quality.py               # как считаем сейчас
    uv run python tools/track_quality.py --stride 1    # покадрово
    uv run python tools/track_quality.py --reid        # с узнаванием по внешности

Отвечает на один вопрос, от которого зависит, есть ли смысл в покадровой
детекции. Решение 001: «теряется не обнаружение, а удержание личности».
Решение 002 уточнило механизм: «человек не разрывается надвое, а поглощается
перекрытием внутри чужого трека». Первое лечится частотой кадров, второе — нет.

Инструмент ничего не чинит и не считает пассажиров. Он печатает числа о следах,
и только их: счёт при смене частоты кадров читать нельзя, пока пороги счёта
заданы в кадрах, а не в секундах (шаг 1 плана).

Наружу уходят только числа (решение 018): кадры не сохраняются.
"""

from __future__ import annotations

import argparse
import sys
from dataclasses import replace
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import case_from_layout  # noqa: E402
from paxcount.bench.counting import DETECT_WINDOW_S, door_specs  # noqa: E402
from paxcount.bench.summary import match_row  # noqa: E402
from paxcount.bench.tracks import tracks_in_windows, window_cache_path  # noqa: E402
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.core.trackdata import load  # noqa: E402
from paxcount.core.video import probe  # noqa: E402
from paxcount.settings import DATA_DIR, detector_for, videos_in  # noqa: E402
from paxcount.stitching import build_remap  # noqa: E402
from paxcount.trackstats import absorption_kinds, track_stats  # noqa: E402
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount import truth_rows  # noqa: E402

console = Console(width=150)


def _video_path(name: str) -> Path | None:
    return next((p for p in videos_in(None) if p.stem == name), None)


def _progress(key: str, idx: int, last: int) -> None:
    if idx % 900 == 0:
        console.print(f"  кадр {idx} из {last}", highlight=False)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doors", type=Path, default=DATA_DIR / "truth" / "doors")
    parser.add_argument("--stride", type=int, default=None,
                        help="шаг детекции; по умолчанию — как для этого видео")
    parser.add_argument("--track-buffer", type=int, default=None,
                        help="память трекера в обработанных кадрах")
    parser.add_argument("--reid", action="store_true",
                        help="узнавать человека по внешности, а не только по месту")
    parser.add_argument("--proximity", type=float, default=None,
                        help="ворота узнавания: ниже — внешность учитывается дальше от места пропажи")
    parser.add_argument("--appearance", type=float, default=None,
                        help="порог сходства внешности")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()

    rows = truth_rows.load(DATA_DIR / "truth" / "rows.csv")

    cases, layouts = {}, {}
    for path in sorted(args.doors.rglob("*.json")):
        layout = load_door_layout(path)
        if layout.camera not in COUNTING_CAMERAS:
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            console.print(f"[yellow]пропущено:[/yellow] {problem}")
            continue
        cases[case.visit_key] = case
        layouts[case.visit_key] = layout

    by_video: dict[str, dict[str, int]] = {}
    for key, case in cases.items():
        by_video.setdefault(case.video, {})[key] = case.frame_idx

    report = []
    for video_name, marks in by_video.items():
        path = _video_path(video_name)
        if path is None:
            console.print(f"[yellow]нет файла {video_name}[/yellow]")
            continue
        settings = detector_for(path)
        if args.stride is not None:
            settings = replace(settings, stride=args.stride)
        if args.track_buffer is not None:
            settings = replace(settings, track_buffer=args.track_buffer)
        if args.reid:
            settings = replace(settings, with_reid=True)
        if args.proximity is not None:
            settings = replace(settings, proximity_thresh=args.proximity)
        if args.appearance is not None:
            settings = replace(settings, appearance_thresh=args.appearance)

        fps = probe(path).fps or 30.0
        half = int(round(DETECT_WINDOW_S * fps))
        windows = {k: (max(0, f - half), f + half) for k, f in marks.items()}
        console.print(f"детекция: {video_name}, окон {len(windows)}, "
                       f"шаг {settings.stride}, память {settings.track_buffer}, "
                       f"узнавание {'да' if settings.with_reid else 'нет'}"
                       + (f", ворота {settings.proximity_thresh}/{settings.appearance_thresh}"
                          if settings.with_reid else ""))
        tracks_in_windows(path, windows, settings=settings, refresh=args.refresh,
                           progress=_progress)

        for key, window in windows.items():
            # Сырые треки, до сшивки: она чинит часть разрывов, и на её выходе
            # не видно, сколько их было. Сколько починила — отдельным числом.
            raw = load(window_cache_path(path, settings, window))
            # Зоны — те же, по которым идёт счёт: полоса ног у каждой двери.
            # Иначе прибор меряет людность улицы, а не работу трекера.
            zones = [s.zone for s in door_specs(layouts[key])] or None
            stats = track_stats(raw, zones=zones)
            split = absorption_kinds(raw, zones=zones)
            row = match_row(cases[key].camera, visit_moment(key), rows)
            # В remap попадают ВСЕ треки, включая корни цепочек (id → сам
            # себе). Сшивка — только те, у кого корень чужой.
            remap = build_remap(raw)
            stitched = sum(1 for tid, root in remap.items() if tid != root)
            report.append((str(row.number) if row else key, stats, stitched, split))

    _print(report, args)
    return 0


def _print(report, args) -> None:
    table = Table(title="Следы людей У ДВЕРЕЙ (сырые, до сшивки)")
    for column in ("визит", "следов", "обрывков", "медиана, с", "передач личности",
                    "поглощений", "ушли за край", "дожили до конца",
                    "сшивок (все следы окна)"):
        table.add_column(column, no_wrap=True)
    total = [0] * 7
    for number, s, stitches, _ in sorted(report, key=lambda r: r[0]):
        table.add_row(number, str(s.tracks), str(s.fragments),
                       f"{s.median_seconds:.2f}", str(s.handovers),
                       str(s.vanishings), str(s.at_border),
                       str(s.alive_at_edge), str(stitches))
        for i, v in enumerate((s.tracks, s.fragments, s.handovers, s.vanishings,
                                s.at_border, s.alive_at_edge, stitches)):
            total[i] += v
    table.add_section()
    table.add_row("всего", str(total[0]), str(total[1]), "—", str(total[2]),
                   str(total[3]), str(total[4]), str(total[5]), str(total[6]))
    console.print(table)
    console.print(
        "[dim]передача личности — след оборвался, рядом родился другой: это разрыв, "
        "его лечит частота кадров.\nпоглощение — оборвался, и никто рядом не родился: "
        "человек слит с чужой рамкой, частота кадров тут ни при чём.[/dim]"
    )
    _print_split(report)


def _print_split(report) -> None:
    """Из чего состоят поглощения — от этого зависит, что чинить."""
    table = Table(title="Поглощения: где человек исчез и чем накрыто место")
    for column in ("визит", "поглощений", "в дверной зоне (посадка?)",
                    "вне зоны (потеря)", "слиянием рамок", "заслонён", "невидим"):
        table.add_column(column, no_wrap=True)
    total = [0] * 6
    for number, _, _, split in sorted(report, key=lambda r: r[0]):
        table.add_row(number, str(split.total), str(split.in_zone),
                       str(split.off_zone), str(split.merged),
                       str(split.occluded), str(split.invisible))
        for i, v in enumerate((split.total, split.in_zone, split.off_zone,
                                split.merged, split.occluded, split.invisible)):
            total[i] += v
    table.add_section()
    table.add_row("всего", *(str(v) for v in total))
    console.print(table)
    console.print(
        "[dim]в дверной зоне — человек пропал там, где садятся: это правдоподобная "
        "посадка, то есть искомое событие, а не дефект.\nслиянием рамок — место накрыла "
        "ВЫРОСШАЯ рамка соседа: детектор человека "
        "видит, но отдал одну рамку на двоих. Это про подавление дубликатов в "
        "детекторе.\nзаслонён и невидим — детектор на этом месте не отдаёт ничего. "
        "Порогами детектора не лечится, нужен признак внешности.[/dim]"
    )


if __name__ == "__main__":
    raise SystemExit(main())
