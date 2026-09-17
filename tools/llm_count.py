"""Счёт людей моделью по дверям каждого варианта: выгрузка и сведение.

    uv run python tools/llm_count.py export          # нарезать пакеты кадров
    uv run python tools/llm_count.py score           # свести ответы с эталоном

Тот же вопрос, что у `bench_count.py` — «какой способ поиска дверей брать», —
но считает не классический счётчик, а мультимодальная модель. Двери берутся
общей функцией (`bench.counting.variants_for`), окно счёта — общей
`stop_window`: сравнивать счётчики можно, только если двери и окно у них одни
и те же, иначе сравниваются две разметки, а не два счётчика.

**Почему файлами, а не вызовом API.** У заказчика подписка, ключа нет. Счётная
модель здесь — слепой субагент, читающий кадры с диска. Кадры ложатся только
под `out/` (`counting/export.py` это стережёт) и в репозиторий не уходят:
запрет 7 разрешает им уходить в счётный API и запрещает публикацию.

**Почему счётчик слепой.** Эталон владельца известен ведущему, и счёт с
оглядкой на него — не замер, а подгонка. Субагент получает кадры и правила
счёта, но не эталон, не наши прежние числа и не таблицу сравнения.

Наружу уходят только числа (решение 018): в `score` печатаются счёт и ошибка,
приметы людей из ответов не берутся никуда.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import case_from_layout  # noqa: E402
from paxcount.bench.counting import (  # noqa: E402
    DETECT_WINDOW_S, VARIANTS, stop_window, variants_for,
)
from paxcount.bench.summary import load_runs, match_row  # noqa: E402
from paxcount.bench.tracks import tracks_in_windows  # noqa: E402
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.core.video import frames as video_frames, probe  # noqa: E402
from paxcount.counting.export import write_packages  # noqa: E402
from paxcount.counting.packages import build_packages  # noqa: E402
from paxcount.counting.parse import AnswerFormatError, consensus, parse_answer  # noqa: E402
from paxcount.counting.prompt import build_count_prompt  # noqa: E402
from paxcount.settings import (  # noqa: E402
    DATA_DIR, OUT_DIR, TARGET_ERROR, detector_for, videos_in,
)
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount.windows import Window  # noqa: E402
from paxcount import truth_counts, truth_rows  # noqa: E402

console = Console(width=190)

LLM_DIR = OUT_DIR / "llm"
ANSWERS_DIR = LLM_DIR / "ответы"
# Кадров в секунду в пакете. Меньше боевых 5: слепой счётчик читает кадры
# файлами, и лента на 5 к/с у окна в 23 с — это 118 картинок на одну дверь.
# Величина — рычаг замера, а не константа метода, поэтому она флагом.
DEFAULT_FPS = 2.0


def _video_path(name: str) -> Path | None:
    return next((p for p in videos_in(None) if p.stem == name), None)


def _progress(key: str, idx: int, last: int) -> None:
    if idx % 900 == 0:
        console.print(f"  кадр {idx} из {last}", highlight=False)


def load_cases(doors_dir: Path):
    """Разметка визитов: только считающие камеры, только разбираемые случаи."""
    layouts, cases = {}, {}
    for path in sorted(doors_dir.rglob("*.json")):
        layout = load_door_layout(path)
        if layout.camera not in COUNTING_CAMERAS:
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            console.print(f"[yellow]пропущено:[/yellow] {problem}")
            continue
        layouts[case.visit_key] = layout
        cases[case.visit_key] = case
    return layouts, cases


def export(args) -> int:
    rows = truth_rows.load(DATA_DIR / "truth" / "rows.csv")
    runs = load_runs(args.bench)
    layouts, cases = load_cases(args.doors)

    by_video: dict[str, dict[str, int]] = {}
    for key, case in cases.items():
        by_video.setdefault(case.video, {})[key] = case.frame_idx

    tracks = {}
    for video_name, marks in by_video.items():
        path = _video_path(video_name)
        if path is None:
            console.print(f"[yellow]нет файла {video_name}[/yellow]")
            continue
        fps = probe(path).fps or 30.0
        half = int(round(DETECT_WINDOW_S * fps))
        windows = {k: (max(0, f - half), f + half) for k, f in marks.items()}
        console.print(f"детекция людей: {video_name}, окон {len(windows)}")
        tracks.update(tracks_in_windows(path, windows, refresh=False,
                                         progress=_progress))

    # Окна счёта — те же, что у классики, и сгруппированы по файлу: лента видео
    # читается один раз на файл, а не по разу на визит.
    plan: dict[str, list[tuple[str, str, tuple[float, float]]]] = {}
    for key, case in sorted(cases.items()):
        row = match_row(case.camera, visit_moment(key), rows)
        number = str(row.number) if row else key
        data = tracks.get(key)
        if data is None:
            console.print(f"[yellow]визит {number}: нет треков[/yellow]")
            continue
        window = stop_window(data, case.body_px)
        if window is None:
            console.print(f"[yellow]визит {number}: окна стоянки нет[/yellow]")
            continue
        plan.setdefault(case.video, []).append((key, number, window))

    total_packages = total_frames = 0
    for video_name, items in plan.items():
        path = _video_path(video_name)
        if path is None:
            continue
        lo = min(w[0] for _, _, w in items)
        hi = max(w[1] for _, _, w in items)
        console.print(f"кадры: {video_name}, {lo:.1f}-{hi:.1f} с")

        # Кадры окон держим в памяти: окно 10-24 с при 2 к/с — это десятки
        # картинок, зато лента видео читается ровно один раз, а не по разу на
        # каждый из пяти вариантов дверей.
        picked: dict[str, list] = {key: [] for key, _, _ in items}
        step = 1.0 / args.fps
        next_at = {key: w[0] for key, _, w in items}
        for frame_idx, ts, picture in video_frames(path):
            if ts > hi:
                break
            for key, _number, window in items:
                if window[0] <= ts <= window[1] and ts >= next_at[key]:
                    picked[key].append((frame_idx, ts, picture.copy()))
                    next_at[key] = ts + step

        for key, number, window in items:
            case, layout = cases[key], layouts[key]
            got = picked.pop(key)
            box = layout.body_px
            win = Window(visit_id=int(number) if number.isdigit() else 0,
                          t0=window[0], t1=window[1], box=box, person_px=None)
            prompt = build_count_prompt(win, fps=args.fps)
            for variant, variant_layout in variants_for(key, case, layout, runs).items():
                if args.variant and variant not in args.variant:
                    continue
                if variant_layout is None:
                    continue
                packages = build_packages(iter(got), win, variant_layout,
                                           fps=args.fps)
                written = write_packages(packages, LLM_DIR, variant,
                                          prompt=prompt, visit=f"визит-{number}")
                total_packages += len(written)
                total_frames += sum(len(w.frames) for w in written)
            del got

    console.print(f"\nпакетов: {total_packages}, кадров: {total_frames}")
    console.print(f"каталог: {LLM_DIR}")
    return 0


def score(args) -> int:
    """Сводит ответы счётчика с эталоном владельца — по вариантам дверей."""
    rows = truth_rows.load(DATA_DIR / "truth" / "rows.csv")
    counts = truth_counts.load(DATA_DIR / "truth" / "door_counts.csv")
    truth_totals = truth_counts.totals(counts)

    answers = _load_answers(args.answers)
    if not answers:
        console.print(f"[yellow]нет ответов в {args.answers}[/yellow]")
        return 1

    numbers = sorted({visit for _, visit, _ in answers}, key=_number_key)
    per_variant: dict[str, dict[str, tuple[int | None, int | None]]] = {}
    disputes: list[str] = []

    for variant in VARIANTS:
        per_visit: dict[str, tuple[int | None, int | None]] = {}
        for number in numbers:
            doors = {door for v, vis, door in answers if v == variant and vis == number}
            if not doors:
                continue
            boarded = alighted = 0
            unknown = False
            for door in sorted(doors):
                verdict = consensus(answers[(variant, number, door)])
                if not verdict.agreed:
                    disputes.append(f"{variant} / {number} / д{door}: {verdict.reason}")
                    unknown = True
                    continue
                boarded += verdict.boarded
                alighted += verdict.alighted
            per_visit[number] = (None, None) if unknown else (boarded, alighted)
        per_variant[variant] = per_visit

    _print_visits(numbers, truth_totals, per_variant)
    _print_error(numbers, truth_totals, per_variant)
    if disputes:
        console.print("\n[yellow]без согласия прогонов (в метрику не идут):[/yellow]")
        for line in disputes:
            console.print(f"  {line}")
    return 0


def _number_key(number: str) -> tuple[int, str]:
    return (int(number), "") if number.isdigit() else (10**6, number)


def _load_answers(root: Path) -> dict:
    """Ответы с диска: `<вариант>/<визит>/д<N>/прогон-<k>.json`."""
    out: dict = {}
    for path in sorted(root.rglob("*.json")):
        rel = path.relative_to(root).parts
        if len(rel) != 4:
            continue
        variant, visit, door_dir, _run = rel
        visit = visit.replace("визит-", "")
        door = int(door_dir.lstrip("д"))
        try:
            answer = parse_answer(path.read_text(encoding="utf-8"))
        except AnswerFormatError as exc:
            console.print(f"[red]{path}: {exc}[/red]")
            continue
        out.setdefault((variant, visit, door), []).append(answer)
    return out


def _cell(pair) -> str:
    if pair is None:
        return ""
    inn, out = pair
    if inn is None or out is None:
        return "?"
    return f"{inn}/{out}"


def _truth_cell(pair) -> str:
    if pair is None:
        return ""
    inn, out = pair
    na = truth_counts.NA
    return f"{na if inn is None else inn}/{na if out is None else out}"


def _print_visits(numbers, truth_totals, per_variant) -> None:
    table = Table(title="Счёт моделью: вошло/вышло по вариантам дверей")
    for column in ("№", "эталон", *VARIANTS):
        table.add_column(column, no_wrap=True)
    for number in numbers:
        table.add_row(
            number, _truth_cell(truth_totals.get(number)),
            *[_cell(per_variant[v].get(number)) for v in VARIANTS],
        )
    console.print(table)
    console.print("[dim]«?» — прогоны разошлись, числа нет (решение 040)[/dim]")


def _print_error(numbers, truth_totals, per_variant) -> None:
    table = Table(title=f"Ошибка против эталона (планка {TARGET_ERROR:.0%})")
    for column in ("вариант", "вошло", "эталон вошло", "ошибка",
                    "вышло", "эталон вышло", "ошибка"):
        table.add_column(column, no_wrap=True)
    for variant in VARIANTS:
        cells = [variant]
        for index in (0, 1):
            mine = truth = 0
            for number in numbers:
                pair = truth_totals.get(number)
                mine_pair = per_variant[variant].get(number)
                if pair is None or pair[index] is None:
                    continue
                if mine_pair is None or mine_pair[index] is None:
                    continue
                truth += pair[index]
                mine += mine_pair[index]
            if truth == 0:
                cells += [str(mine), "—", "—"]
                continue
            error = abs(mine - truth) / truth
            mark = "[green]" if error <= TARGET_ERROR else "[red]"
            cells += [str(mine), str(truth), f"{mark}{error:.0%}[/]"]
        table.add_row(*cells)
    console.print(table)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    ex = sub.add_parser("export", help="нарезать пакеты кадров под out/llm")
    ex.add_argument("--doors", type=Path, default=DATA_DIR / "truth" / "doors")
    ex.add_argument("--bench", type=Path, default=OUT_DIR / "bench_doors.json")
    ex.add_argument("--fps", type=float, default=DEFAULT_FPS)
    ex.add_argument("--variant", action="append",
                     help="только этот вариант дверей (можно повторять)")
    ex.set_defaults(func=export)

    sc = sub.add_parser("score", help="свести ответы счётчика с эталоном")
    sc.add_argument("--answers", type=Path, default=ANSWERS_DIR)
    sc.set_defaults(func=score)

    args = parser.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
