"""Стенд сравнения методов локализации дверей.

Запуск:

    uv run python tools/bench_doors.py --check          # только готовность
    uv run python tools/bench_doors.py                  # все доступные методы
    uv run python tools/bench_doors.py -m edges -m pixels

Вход — ручная разметка эталона (`data/truth/doors`). Ни детектор, ни кэш
треков не участвуют: рамка кузова берётся из разметки, потому что сравнивать
методы на разных рамках бессмысленно.

Наружу уходят только числа (решение 018): кадры не сохраняются, в отчёт идут
координаты, попадания и время работы.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import Case, load_cases  # noqa: E402
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.bench.methods import DEFERRED, DESCRIPTIONS, REGISTRY  # noqa: E402
from paxcount.bench.run import FAILED, MISSING, OK, run_case  # noqa: E402
from paxcount.bench.video import VideoFrames  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR, videos_in  # noqa: E402

console = Console()
DOORS_DIR = DATA_DIR / "truth" / "doors"


def _video_path(case: Case) -> Path | None:
    for path in videos_in(None):
        if path.stem == case.video:
            return path
    return None


def _queries_for(case: Case, cases: list[Case], frames_by_video) -> list:
    """Вырезки дверей с ДРУГИХ визитов — образцы для OWLv2.

    Дверь того же визита была бы подсказкой, которой в бою нет: метод получил
    бы кусок того самого кадра, на котором его проверяют.
    """
    queries = []
    for other in cases:
        if other.visit_key == case.visit_key:
            continue
        frames = frames_by_video.get(other.video)
        if frames is None:
            continue
        image = frames.frame(other.frame_idx)
        x0, y0, x1, y1 = (int(round(v)) for v in other.doors_visible[0])
        crop = image[max(0, y0):y1, max(0, x0):x1]
        if crop.size:
            queries.append(crop)
    return queries[:2]  # больше образцов — линейно дороже, выигрыша не видно


def check(tags: list[str]) -> None:
    table = Table(title="Готовность методов", show_lines=False)
    table.add_column("метод"); table.add_column("что это")
    table.add_column("состояние"); table.add_column("чего не хватает")
    for tag in tags:
        finder = REGISTRY[tag]()
        reason = finder.available()
        table.add_row(tag, DESCRIPTIONS[tag],
                       "[green]готов[/green]" if reason is None else "[yellow]нет[/yellow]",
                       reason or "")
    console.print(table)
    if DEFERRED:
        console.print("\n[dim]Отложено владельцем:[/dim]")
        for tag, reason in DEFERRED.items():
            console.print(f"  [dim]{tag}: {reason}[/dim]")


def export_frames(doors_dir: Path, target: Path) -> int:
    """Кадры случаев с обведённой рамкой кузова — для разбора глазами.

    Нужны там, где метод отвечает не кодом: мультимодальная модель смотрит на
    эти же кадры в сессии, а не по API (ключа у проекта нет). Кадры пишутся
    только под `out/`, который закрыт в .gitignore: запрет 7 — кадры не
    публикуются ни в репозиторий, ни в артефакты.
    """
    import cv2

    if OUT_DIR.resolve() not in target.resolve().parents and target.resolve() != OUT_DIR.resolve():
        console.print(f"[red]выгрузка разрешена только под {OUT_DIR}/[/red] — "
                       "кадры содержат лица и номера машин (запрет 7)")
        return 2

    cases, skipped = load_cases(doors_dir, cameras=COUNTING_CAMERAS)
    for note in skipped:
        console.print(f"[yellow]пропущено:[/yellow] {note}")
    if not cases:
        console.print(f"[red]нет ни одного случая в {doors_dir}[/red]")
        return 1

    target.mkdir(parents=True, exist_ok=True)
    for case in cases:
        path = _video_path(case)
        if path is None:
            console.print(f"[yellow]нет файла {case.video}[/yellow]")
            continue
        image = VideoFrames(path).frame(case.frame_idx).copy()
        x0, y0, x1, y1 = (int(round(v)) for v in case.body_px)
        cv2.rectangle(image, (x0, y0), (x1, y1), (255, 160, 60), 3)
        slug = case.visit_key.replace("/", "_").replace(":", "-").strip("_")
        out = target / f"{slug}.jpg"
        cv2.imwrite(str(out), image, [cv2.IMWRITE_JPEG_QUALITY, 92])
        console.print(f"{out}  кадр {case.frame_idx}, кузов {x0}..{x1}, "
                       f"дверей в кадре {len(case.doors_visible)}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-m", "--method", action="append", dest="methods",
                        help="метод (можно повторять); по умолчанию — все")
    parser.add_argument("--check", action="store_true",
                        help="только проверить готовность, ничего не запускать")
    parser.add_argument("--doors", type=Path, default=DOORS_DIR)
    parser.add_argument("--out", type=Path, default=OUT_DIR / "bench_doors.json")
    parser.add_argument("--export", type=Path, metavar="КАТАЛОГ",
                        help="выгрузить кадры случаев с рамкой кузова и выйти "
                             "(только под out/ — кадры наружу не уходят)")
    args = parser.parse_args()

    tags = args.methods or list(REGISTRY)
    unknown = [t for t in tags if t not in REGISTRY]
    if unknown:
        console.print(f"[red]неизвестные методы: {unknown}[/red]")
        return 2

    if args.check:
        check(tags)
        return 0

    if args.export:
        return export_frames(args.doors, args.export)

    cases, skipped = load_cases(args.doors, cameras=COUNTING_CAMERAS)
    for note in skipped:
        console.print(f"[yellow]пропущено:[/yellow] {note}")
    if not cases:
        console.print(f"[red]нет ни одного случая в {args.doors}[/red] — "
                       "разметьте визиты на /markup")
        return 1

    frames_by_video = {}
    for case in cases:
        if case.video in frames_by_video:
            continue
        path = _video_path(case)
        if path is None:
            console.print(f"[yellow]нет файла {case.video} — визит {case.visit_key} "
                           "пропущен[/yellow]")
            continue
        frames_by_video[case.video] = VideoFrames(path)

    runnable = [c for c in cases if c.video in frames_by_video]
    console.print(f"случаев: {len(runnable)}, методов: {len(tags)}")

    results = []
    for tag in tags:
        finder = REGISTRY[tag]()
        for case in runnable:
            if tag == "owlv2":
                finder.queries = _queries_for(case, runnable, frames_by_video)
            result = run_case(case, finder, frames_by_video[case.video])
            results.append(result)
            if result.status == MISSING:
                break  # причина одна на все случаи — не повторять её пять раз

    _report(results, tags, runnable)
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(json.dumps([
        {
            "method": r.method, "visit_key": r.case.visit_key, "status": r.status,
            "note": r.note, "elapsed_s": round(r.elapsed_s, 3),
            "total": r.score.total if r.score else None,
            "found": r.score.found if r.score else None,
            "extras": r.score.extras if r.score else None,
            "center_error_px": (round(r.score.mean_center_error_px, 1)
                                 if r.score and r.score.mean_center_error_px is not None
                                 else None),
            "iou_x": (round(r.score.mean_iou_x, 3)
                       if r.score and r.score.mean_iou_x is not None else None),
            "predicted": [[round(v, 1) for v in b] for b in r.predicted],
            "scores": [round(float(s), 4) for s in r.scores],
        }
        for r in results
    ], ensure_ascii=False, indent=2), encoding="utf-8")
    console.print(f"\nподробности: {args.out}")
    return 0


def _report(results, tags, cases) -> None:
    table = Table(title="Итог по методам", show_lines=False)
    for column in ("метод", "состояние", "дверей", "найдено", "лишних",
                    "центр, px", "IoU x", "с/визит", "заметка"):
        table.add_column(column)

    for tag in tags:
        rows = [r for r in results if r.method == tag]
        if not rows:
            continue
        ok = [r for r in rows if r.status == OK]
        if not ok:
            bad = rows[0]
            table.add_row(tag, bad.status, "", "", "", "", "", "",
                           bad.note[:80])
            continue
        total = sum(r.score.total for r in ok)
        found = sum(r.score.found for r in ok)
        extras = sum(r.score.extras for r in ok)
        errors = [r.score.mean_center_error_px for r in ok
                   if r.score.mean_center_error_px is not None]
        ious = [r.score.mean_iou_x for r in ok if r.score.mean_iou_x is not None]
        failed = sum(r.status == FAILED for r in rows)
        table.add_row(
            tag, "ок" + (f" ({failed} упало)" if failed else ""),
            str(total), f"{found} ({found / total:.0%})" if total else "0",
            str(extras),
            f"{sum(errors) / len(errors):.0f}" if errors else "—",
            f"{sum(ious) / len(ious):.2f}" if ious else "—",
            f"{sum(r.elapsed_s for r in ok) / len(ok):.1f}",
            (ok[0].note or "")[:40],
        )
    console.print(table)

    detail = Table(title="По визитам: найдено из видимых дверей")
    detail.add_column("метод")
    for case in cases:
        detail.add_column(case.visit_key.split("/")[-1])
    for tag in tags:
        rows = {r.case.visit_key: r for r in results if r.method == tag}
        cells = []
        for case in cases:
            r = rows.get(case.visit_key)
            if r is None or r.status != OK:
                cells.append("—")
            else:
                cells.append(f"{r.score.found}/{r.score.total}"
                              + (f" +{r.score.extras}" if r.score.extras else ""))
        if any(c != "—" for c in cells):
            detail.add_row(tag, *cells)
    console.print(detail)


if __name__ == "__main__":
    raise SystemExit(main())
