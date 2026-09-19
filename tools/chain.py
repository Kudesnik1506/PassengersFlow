"""Цепочка проездов по камере 1 и сверка её с оператором и счётными камерами.

    uv run python tools/chain.py --stop 22739 --part Утро

Оператор пропускает машины, и пропуск виден только со стороны. К2 и К3 видят,
что машина СТОЯЛА, но не знают, какая это машина (решение 003). К1 читает борт
с подъезжающей морды и держит порядок, но стоянку от проезда не отличает
(решение 030). Здесь три свидетельства выравниваются между собой, и остаются
звенья, которых нет ни у кого: их и ищем.

Кадры не сохраняются: опознание — отдельный шаг, и кадры там ложатся только
под `out/` (запрет 6).
"""

from __future__ import annotations

import argparse
import csv
import dataclasses
import sys
from datetime import datetime, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.delivery.chain import (  # noqa: E402
    MIN_PAIRS, Passage, align, fitted_shift, passages,
)
from paxcount.delivery.operator import drop_duplicates, for_stop, read_export  # noqa: E402
from paxcount.delivery.timeline import parse_slot  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR  # noqa: E402

console = Console()

CHAIN_DIR = OUT_DIR / "chain"
# Затравка сдвига «часы К1 минус часы оператора». Точное значение подгоняется
# на каждом файле, здесь только с чего начать поиск пар.
SEED_SHIFT_S = 80.0
WIDE_TOLERANCE_S = 120.0
TIGHT_TOLERANCE_S = 45.0


def videos_of(camera: str, part: str | None) -> list[Path]:
    root = DATA_DIR / "prod_videos"
    found = [p for p in sorted(root.rglob("*.MP4")) if f"_{camera} - " in p.name]
    if part:
        found = [p for p in found if p.parent.name == part]
    return found


def passages_of(video: Path) -> list[Passage]:
    """Проезды одного файла. Треки берутся из кэша, детекция не гоняется."""
    from paxcount.settings import detector_for
    from paxcount.tracking import cache_path, get_tracks
    from paxcount.visits import matches_class, vehicle_tracks

    settings = detector_for(video)
    if not cache_path(video, settings).exists():
        console.print(f"[yellow]{video.name}: треков нет, файл пропущен — "
                       "сначала детекция[/yellow]")
        return []
    data, _, _ = get_tracks(video, settings)
    tracks = {i: t for i, t in vehicle_tracks(data).items()
               if matches_class(t, ["bus", "truck", "train"])}
    slot = parse_slot(video.name)
    return passages(tracks, camera=slot.camera, file=video.stem,
                     start=slot.start, frame_size=(data.width, data.height))


def machines_in(records: list, start: datetime, end: datetime) -> list:
    """Машины оператора в окне: повторные нажатия сняты (решение 075)."""
    window = [r for r in records if start <= r.created <= end]
    return drop_duplicates(window).kept


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stop", required=True, help="номер ОП")
    parser.add_argument("--camera", default="1", help="камера цепочки")
    parser.add_argument("--part", default=None, help="папка смены: Утро, День, Вечер")
    parser.add_argument("--operator-export", type=Path,
                         default=DATA_DIR / "prod_videos" / "видео 1"
                                 / "2026-09-14_Fixatsia_transporta_na_ostanovkakh.xlsx")
    args = parser.parse_args()

    records = for_stop(read_export(args.operator_export), args.stop)
    videos = videos_of(args.camera, args.part)
    if not videos:
        console.print("[red]записей камеры не нашлось[/red]")
        return 2
    console.print(f"файлов камеры {args.camera}: {len(videos)}, "
                   f"нажатий оператора за сутки: {len(records)}")

    CHAIN_DIR.mkdir(parents=True, exist_ok=True)
    seen: list[Passage] = []
    shifts: list[dict] = []
    lines: list[dict] = []

    for video in videos:
        found = passages_of(video)
        if not found:
            continue
        seen += found
        # Окно оператора — по самим проездам, а не по длине файла: длительность
        # слота известна только при сборке смены, а границы езды видны прямо.
        seed = timedelta(seconds=SEED_SHIFT_S)
        margin = timedelta(seconds=WIDE_TOLERANCE_S)
        machines = machines_in(records,
                                found[0].start - seed - margin,
                                found[-1].end - seed + margin)
        left = [p.peak for p in found]
        right = [m.created for m in machines]
        if not right:
            console.print(f"[yellow]{video.stem}: записей оператора в окне нет[/yellow]")
            continue

        rough = align(left, right, shift=seed,
                       tolerance=timedelta(seconds=WIDE_TOLERANCE_S))
        try:
            shift = fitted_shift(left, right, rough, minimum=MIN_PAIRS)
        except ValueError as why:
            console.print(f"[red]{video.stem}: {why}[/red]")
            shifts.append({"файл": video.stem, "сдвиг_с": "", "пар": 0,
                            "причина": str(why)})
            continue
        rows = align(left, right, shift=shift,
                      tolerance=timedelta(seconds=TIGHT_TOLERANCE_S))
        pairs = [(i, j) for i, j in rows if i is not None and j is not None]
        shifts.append({"файл": video.stem, "сдвиг_с": f"{shift.total_seconds():.0f}",
                        "пар": len(pairs), "причина": ""})

        for i, j in rows:
            passage = found[i] if i is not None else None
            machine = machines[j] if j is not None else None
            lines.append({
                "файл": video.stem,
                "проезд": passage.peak.isoformat() if passage else "",
                "трек": passage.track if passage else "",
                "секунд": f"{(passage.end - passage.start).total_seconds():.0f}" if passage else "",
                "ширина": f"{passage.box[2] - passage.box[0]:.0f}" if passage else "",
                "оператор": machine.created.isoformat() if machine else "",
                "маршрут": machine.route if machine else "",
                "борт": machine.board if machine else "",
                "остаток_с": (f"{(passage.peak - machine.created - shift).total_seconds():+.0f}"
                               if passage and machine else ""),
            })

    write(CHAIN_DIR / f"{args.stop}-К{args.camera}.csv",
           [dataclasses.asdict(p) | {"box": list(p.box)} for p in seen])
    write(CHAIN_DIR / f"{args.stop}-сдвиги.csv", shifts)
    write(CHAIN_DIR / f"{args.stop}-лента.csv", lines)

    orphans = [line for line in lines if line["проезд"] and not line["оператор"]]
    missed = [line for line in lines if line["оператор"] and not line["проезд"]]
    table = Table(title="Ничьи проезды: машина проехала, оператор не записал")
    for column in ("файл", "проезд", "секунд", "ширина", "трек"):
        table.add_column(column, no_wrap=True)
    for line in orphans:
        table.add_row(line["файл"][-2:], line["проезд"][11:19], line["секунд"],
                       line["ширина"], str(line["трек"]))
    console.print(table)
    console.print(f"проездов {len(seen)}, спарено "
                   f"{sum(1 for x in lines if x['проезд'] and x['оператор'])}, "
                   f"ничьих проездов {len(orphans)}, "
                   f"записей без проезда {len(missed)}")
    console.print(f"[dim]{CHAIN_DIR}/[/dim]")
    return 0


def write(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
