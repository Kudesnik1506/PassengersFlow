"""Командный интерфейс paxcount."""

from __future__ import annotations

import time
from pathlib import Path

import typer
from rich.console import Console
from rich.table import Table

from .core.detect import pick_device
from .core.trackdata import cache_path
from .core.types import RunResult
from .core.video import probe
from .core.writer import write_run
from .doors import is_manual, load_config, save_config
from .runlog import setup_logger
from .settings import (
    DETECTOR, OUT_DIR, TRUTH_PATH, VIDEO_DIR, ZONE_FRACTION_MAX, ZONE_FRACTION_MIN,
    ZONE_MIN_BOTTOM, ZONE_MIN_TOP, DetectorSettings, videos_in,
)
from .tracking import get_tracks

app = typer.Typer(add_completion=False, help="Подсчёт пассажиропотока по видео.")
console = Console()
log = setup_logger("cli")

BACKENDS = ("custom", "sv_linezone")


def _make_backend(name: str):
    if name == "custom":
        from .backends.custom import CustomBackend

        return CustomBackend()
    if name == "sv_linezone":
        from .backends.sv_linezone import SupervisionLineBackend

        return SupervisionLineBackend()
    raise typer.BadParameter(f"неизвестный бэкенд: {name}. Доступны: {', '.join(BACKENDS)}")


def _settings(weights: str, imgsz: int, tracker: str, stride: int) -> DetectorSettings:
    return DetectorSettings(
        weights=weights, imgsz=imgsz, tracker=tracker, stride=stride, conf=DETECTOR.conf
    )


@app.command()
def info(target: Path = typer.Argument(VIDEO_DIR)) -> None:
    """Показывает параметры видео, наличие ручной разметки и кэша."""
    table = Table(title="Видео")
    for col in ("файл", "разрешение", "fps", "длительность", "разметка", "кэш"):
        table.add_column(col)
    for video in videos_in(target):
        meta = probe(video)
        table.add_row(
            video.name, f"{meta.width}x{meta.height}", f"{meta.fps:.2f}",
            f"{meta.duration_s:.1f} с",
            "ручная" if is_manual(video) else "авто",
            "есть" if cache_path(video).exists() else "нет",
        )
    console.print(table)
    console.print(f"устройство: [bold]{pick_device()}[/bold]")


@app.command()
def check(target: Path = typer.Argument(VIDEO_DIR), truth: Path = typer.Option(TRUTH_PATH)) -> None:
    """Детерминированная самопроверка набора: файлы, эталон, разметка, кэш.

    Всё, что можно проверить кодом, проверяется кодом — модель для этого не
    нужна: имена файлов, читаемость видео, ссылки эталона и зон на
    существующие файлы, совпадение версии кэша.
    """
    import csv

    problems: list[str] = []
    videos = videos_in(target)
    names = {v.name for v in videos}

    if not videos:
        problems.append(f"в {target} нет видео")
    for video in videos:
        try:
            meta = probe(video)
            if meta.frame_count <= 0:
                problems.append(f"{video.name}: не читается число кадров")
        except Exception as exc:  # noqa: BLE001
            problems.append(f"{video.name}: {exc}")

    if truth.exists():
        with truth.open(encoding="utf-8") as fh:
            for row in csv.DictReader(fh):
                if row["video"] not in names:
                    problems.append(f"эталон ссылается на отсутствующее видео: {row['video']}")
                for field in ("boarded", "alighted"):
                    if not row[field].isdigit():
                        problems.append(f"эталон: {row['video']} — {field} не число")
    else:
        problems.append(f"нет эталона: {truth}")

    from .settings import SYNTHETIC_DIR, ZONES_DIR

    if ZONES_DIR.exists():
        stems = {v.stem for v in videos}
        if SYNTHETIC_DIR.exists():
            stems |= {v.stem for v in videos_in(SYNTHETIC_DIR)}
        for zone in ZONES_DIR.glob("*.json"):
            # Файлы автопредложения — <stem>.auto.json и версионированный кэш
            # <stem>.auto__p1_<метод>_<хеш>.json: у них свой суффикс, но видео
            # то же самое.
            base = zone.name.split(".auto")[0].removesuffix(".json")
            if base not in stems:
                problems.append(f"разметка без видео: {zone.name}")

        from .core.types import VideoConfig

        for zone in ZONES_DIR.glob("*.json"):
            if ".auto" in zone.name:
                continue
            cfg = VideoConfig.model_validate_json(zone.read_text(encoding="utf-8"))
            # reference_box нужен, чтобы `doors remap` мог перенести зоны при
            # смене конвенции канонического bbox. Для конфигов с door_sets он
            # не определён (наборов несколько, опорная рамка у каждого своя) —
            # remap такие конфиги пока не поддерживает, и требовать поле,
            # которого он всё равно не прочитает, было бы ложной строгостью.
            if cfg.manual and cfg.reference_box is None and not cfg.door_sets:
                problems.append(
                    f"{zone.name}: ручная разметка без reference_box — "
                    "пиксельный смысл зон не зафиксирован, doors remap не сможет их перенести"
                )
            all_doors = list(cfg.doors) + [d for ds in cfg.door_sets for d in ds.doors]
            for d in all_doors:
                if d.mode != "zone":
                    continue
                x0, y0, x1, y1 = d.zone
                if not (x0 < x1 and y0 < y1):
                    problems.append(f"{zone.name}: дверь {d.door_id} — zone не x0<x1, y0<y1")
                if any(v < ZONE_FRACTION_MIN or v > ZONE_FRACTION_MAX for v in d.zone):
                    problems.append(
                        f"{zone.name}: дверь {d.door_id} — доля zone вне "
                        f"[{ZONE_FRACTION_MIN}, {ZONE_FRACTION_MAX}] "
                        f"({d.zone}), похоже на опечатку"
                    )
                # Счёт привязан к точке опоры человека — ногам на земле, а низ
                # рамки ТС проходит по колёсам. Зона, нарисованная строго по
                # дверному проёму, кончается на уровне пола салона и не ловит
                # ни одного человека: на 09 такая разметка дала 0 событий при
                # реальной посадке (замер по ролику: ноги стоящих у автобуса
                # людей приходятся на 0.74..1.14 высоты рамки, медиана 0.97).
                if y0 < ZONE_MIN_TOP:
                    problems.append(
                        f"{zone.name}: дверь {d.door_id} — верх зоны {y0:.2f} выше "
                        f"{ZONE_MIN_TOP}: зона захватывает салон и будет ловить "
                        "пассажиров, видимых сквозь дверь (нужно ~0.65)"
                    )
                if y1 < ZONE_MIN_BOTTOM:
                    problems.append(
                        f"{zone.name}: дверь {d.door_id} — низ зоны {y1:.2f} выше "
                        f"{ZONE_MIN_BOTTOM}: зона обрывается над землёй и не поймает "
                        "ноги стоящего у двери человека (нужно ~1.1)"
                    )

    if problems:
        for p in problems:
            console.print(f"[red]✗[/red] {p}")
        raise typer.Exit(1)
    console.print(f"[green]✓[/green] проверено видео: {len(videos)}, замечаний нет")


@app.command()
def run(
    target: Path = typer.Argument(VIDEO_DIR, help="Видео или папка."),
    backend: str = typer.Option("custom", help=f"Один из: {', '.join(BACKENDS)}."),
    out: Path = typer.Option(OUT_DIR, help="Куда складывать результаты."),
    debug: bool = typer.Option(False, help="Писать отладочное видео с боксами."),
    stride: int = typer.Option(DETECTOR.stride, help="Брать каждый N-й кадр."),
    tracker: str = typer.Option(DETECTOR.tracker, help="Трекер ultralytics."),
    weights: str = typer.Option(DETECTOR.weights),
    imgsz: int = typer.Option(DETECTOR.imgsz),
    device: str = typer.Option(None, help="mps / cpu / cuda. По умолчанию — авто."),
    refresh: bool = typer.Option(False, help="Перестроить кэш треков заново."),
) -> None:
    """Считает пассажиров и пишет visits.csv, events.csv, run.json."""
    engine = _make_backend(backend)
    settings = _settings(weights, imgsz, tracker, stride)
    run_id = time.strftime("%Y%m%d-%H%M%S")
    results: list[RunResult] = []

    for video in videos_in(target):
        config = load_config(video)
        data, cached, elapsed = get_tracks(
            video, settings=settings, device=device, refresh=refresh
        )
        result = engine.run(data, config)
        out_dir = out / run_id / backend / video.stem
        paths = write_run(result, out_dir)
        results.append(result)
        log.info(
            "%s · %s · вошло %d, вышло %d, визитов %d",
            video.name, backend, result.boarded, result.alighted, len(result.visits),
        )
        console.print(
            f"[cyan]{video.name}[/cyan] · {'кэш' if cached else f'детекция {elapsed:.1f} с'}"
            f" · счёт {result.wall_seconds:.2f} с — визитов {len(result.visits)}, "
            f"вошло {result.boarded}, вышло {result.alighted}"
        )

        if debug:
            from .render import render_debug
            from .visits import build_scene

            boxes = build_scene(data, config).boxes
            path = render_debug(video, data, config, result, out_dir / "debug.mp4", boxes)
            console.print(f"  отладочное видео: {path}")

    _summary(results)
    console.print(f"\nрезультаты: [bold]{out / run_id / backend}[/bold]")


def _summary(results: list[RunResult]) -> None:
    table = Table(title="Итог")
    for col in ("видео", "визитов", "вошло", "вышло", "кадр/с"):
        table.add_column(col)
    for r in results:
        speed = r.frames_processed / r.wall_seconds if r.wall_seconds else 0
        table.add_row(
            r.video, str(len(r.visits)), str(r.boarded), str(r.alighted), f"{speed:.0f}"
        )
    console.print(table)


@app.command()
def bench(
    target: Path = typer.Argument(VIDEO_DIR),
    out: Path = typer.Option(OUT_DIR),
    truth: Path = typer.Option(TRUTH_PATH),
    stride: int = typer.Option(DETECTOR.stride),
    tracker: str = typer.Option(DETECTOR.tracker),
    weights: str = typer.Option(DETECTOR.weights),
    imgsz: int = typer.Option(DETECTOR.imgsz),
) -> None:
    """Прогоняет все бэкенды по одному набору и сравнивает с эталоном."""
    from .evaluate import compare_events, compare_runs
    from .settings import DATA_DIR

    settings = _settings(weights, imgsz, tracker, stride)
    run_id = time.strftime("%Y%m%d-%H%M%S") + "-bench"
    for video in videos_in(target):
        data, _, _ = get_tracks(video, settings=settings)
        config = load_config(video)
        for name in BACKENDS:
            result = _make_backend(name).run(data, config)
            write_run(result, out / run_id / name / video.stem)
            console.print(
                f"[cyan]{video.name}[/cyan] · {name}: "
                f"вошло {result.boarded}, вышло {result.alighted}"
            )
    console.print()
    compare_runs(out / run_id, truth, console)
    events_truth = DATA_DIR / "truth" / "events.csv"
    if events_truth.exists():
        console.print()
        compare_events(out / run_id, events_truth, console)
    console.print(f"\nпрогон: [bold]{out / run_id}[/bold]")


@app.command()
def compare(
    run_dir: Path = typer.Argument(..., help="Папка прогона, например out/20260907-2210-bench."),
    truth: Path = typer.Option(TRUTH_PATH),
) -> None:
    """Сравнивает прогоны с эталоном и печатает метрики."""
    from .evaluate import compare_runs

    compare_runs(run_dir, truth, console)


@app.command()
def baseline(
    target: Path = typer.Argument(VIDEO_DIR),
    save: bool = typer.Option(False, help="Записать текущие числа как новый baseline."),
) -> None:
    """Сравнивает текущие числа custom-бэкенда с замороженным baseline.

    Без --save падает с ненулевым кодом, если что-то изменилось — удобно
    вызывать после правок логики счёта, не обязательно вручную.
    """
    from .baseline import BASELINE_PATH, compute_baseline, diff_baseline, load_baseline, save_baseline

    current = compute_baseline(target)
    if save:
        path = save_baseline(current)
        console.print(f"[green]baseline сохранён:[/green] {path}")
        return
    try:
        old = load_baseline()
    except FileNotFoundError as exc:
        console.print(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc
    changed = diff_baseline(old, current, console)
    if changed:
        console.print(
            f"[yellow]есть расхождения с {BASELINE_PATH}[/yellow] — "
            "если это ожидаемо, зафиксируйте: paxcount baseline --save"
        )
        raise typer.Exit(1)
    console.print("[green]✓ совпадает с baseline[/green]")


@app.command("compare-events")
def compare_events_cmd(
    run_dir: Path = typer.Argument(..., help="Папка прогона, например out/20260907-2210-bench."),
    truth: Path = typer.Option(Path("data/truth/events.csv")),
    window: float = typer.Option(1.5, help="Окно сопоставления по времени, секунды."),
) -> None:
    """Precision/recall/F1 по событиям — в отличие от compare видит перелёт
    и недолёт по отдельности, а не только их разность."""
    from .evaluate import compare_events

    compare_events(run_dir, truth, console, window=window)


@app.command()
def diagnose(
    video: Path = typer.Argument(...),
    stride: int = typer.Option(DETECTOR.stride),
    tracker: str = typer.Option(DETECTOR.tracker),
    weights: str = typer.Option(DETECTOR.weights),
    imgsz: int = typer.Option(DETECTOR.imgsz),
    door: bool = typer.Option(
        False, help="Плюс фрагментация треков и причины отказа classify() — без эталона."
    ),
) -> None:
    """Показывает, почему получилось именно столько: треки, скорости, визиты."""
    from .diagnose import diagnose as run_diagnose

    data, cached, _ = get_tracks(video, settings=_settings(weights, imgsz, tracker, stride))
    console.print(
        f"{video.name} · {'кэш' if cached else 'детекция'} · {len(data.frames)} кадров"
    )
    run_diagnose(data, load_config(video), console, door=door)


doors_app = typer.Typer(help="Автопредложение геометрии дверей.")
app.add_typer(doors_app, name="doors")

PROPOSAL_METHODS = ("pixels", "openvocab", "both")


def _proposers(method: str):
    from .doorprop.openvocab import OpenVocabDoorProposer
    from .doorprop.pixels import PixelDoorProposer

    if method == "pixels":
        return [PixelDoorProposer()]
    if method == "openvocab":
        return [OpenVocabDoorProposer()]
    if method == "both":
        return [PixelDoorProposer(), OpenVocabDoorProposer()]
    raise typer.BadParameter(
        f"неизвестный метод: {method}. Доступны: {', '.join(PROPOSAL_METHODS)}"
    )


def _proposal_context(video: Path):
    """Опорные кадры и bbox ТС для самого длинного визита видео."""
    from .doorprop.frames import pick_proposal_frames
    from .settings import DOOR_PROPOSAL
    from .visits import build_scene

    data, _, _ = get_tracks(video)
    cfg = load_config(video)
    scene = build_scene(data, cfg)
    if scene.primary is None:
        raise typer.BadParameter(f"{video.name}: в кадре не найдено ТС")
    if not scene.visits:
        raise typer.BadParameter(f"{video.name}: не найдено ни одного визита ТС")
    longest = max(scene.visits, key=lambda v: v.departure_ts - v.arrival_ts)
    # Рамки берутся у ТС ИМЕННО этого визита: визитов на ролике теперь много и
    # самый длинный не обязан принадлежать главному ТС сцены.
    boxes = scene.boxes_for(longest)
    frames = pick_proposal_frames(data, boxes, longest, k=DOOR_PROPOSAL.reference_frames)
    return data, cfg, boxes, frames


def _truncation(data, boxes, frames) -> tuple[bool, bool]:
    """Обрезан ли bbox ТС левым или правым краем кадра на опорных кадрах."""
    from .zonecount import BORDER_MARGIN

    left = right = False
    for idx in frames:
        box = boxes.get(idx)
        if box is None:
            continue
        left = left or box[0] <= BORDER_MARGIN
        right = right or box[2] >= data.width - BORDER_MARGIN
    return left, right


@doors_app.command("propose")
def doors_propose(
    target: Path = typer.Argument(VIDEO_DIR, help="Видео или папка."),
    method: str = typer.Option("pixels", help=f"Один из: {', '.join(PROPOSAL_METHODS)}."),
    save: bool = typer.Option(True, help="Записать предложение в data/zones/<stem>.auto.json."),
) -> None:
    """Предлагает зоны дверей. Ручная разметка всегда побеждает предложение."""
    from .core.types import DoorSpec, Point
    from .doorprop import propose_doors
    from .doors import save_proposal

    for video in videos_in(target):
        data, cfg, boxes, frames = _proposal_context(video)
        left, right = _truncation(data, boxes, frames)
        for proposer in _proposers(method):
            doors = propose_doors(
                proposer, video, data, frames, boxes,
                truncated_left=left, truncated_right=right,
            )
            console.print(
                f"[cyan]{video.name}[/cyan] · {proposer.method_tag}: "
                f"дверей найдено {len(doors)} (опорных кадров {len(frames)})"
            )
            for i, d in enumerate(doors, 1):
                console.print(
                    f"    door{i}: x {d.x0:.3f}–{d.x1:.3f}, y {d.y0:.3f}–{d.y1:.3f}, "
                    f"score {d.score:.3f}"
                )
            if not save or not doors:
                continue
            proposal = cfg.model_copy(deep=True)
            proposal.video = video.name
            proposal.manual = False
            proposal.doors = [
                DoorSpec(
                    door_id=f"door{i + 1}", mode="zone", frame="vehicle",
                    line_start=Point(x=d.x0, y=d.y1), line_end=Point(x=d.x1, y=d.y1),
                    zone=(round(d.x0, 4), round(d.y0, 4), round(d.x1, 4), round(d.y1, 4)),
                )
                for i, d in enumerate(doors)
            ]
            path = save_proposal(proposal, video, proposer.method_tag)
            log.info("%s · %s: предложение записано в %s", video.name, proposer.method_tag, path)
            if is_manual(video):
                console.print(
                    "    [yellow]есть ручная разметка — она и будет использоваться[/yellow]"
                )


@doors_app.command("eval")
def doors_eval(
    target: Path = typer.Argument(VIDEO_DIR),
    method: str = typer.Option("both", help=f"Один из: {', '.join(PROPOSAL_METHODS)}."),
    iou: float = typer.Option(0.5, help="Порог 1D-IoU вдоль x, при котором дверь считается найденной."),
) -> None:
    """Сравнивает методы автопредложения с ручной разметкой по 1D-IoU вдоль x."""
    from .doorprop import propose_doors
    from .doors import config_path

    table = Table(title=f"Автопредложение против ручной разметки (IoU ≥ {iou})")
    for col in ("видео", "метод", "ручных", "предложено", "совпало", "промахов"):
        table.add_column(col)

    for video in videos_in(target):
        if not config_path(video).exists():
            continue
        manual = load_config(video).doors
        manual_x = [(d.zone[0], d.zone[2]) for d in manual]
        data, _, boxes, frames = _proposal_context(video)
        left, right = _truncation(data, boxes, frames)
        for proposer in _proposers(method):
            doors = propose_doors(
                proposer, video, data, frames, boxes,
                truncated_left=left, truncated_right=right,
            )
            matched = sum(
                1 for m in manual_x
                if any(_iou_1d(m, (d.x0, d.x1)) >= iou for d in doors)
            )
            table.add_row(
                video.name, proposer.method_tag, str(len(manual_x)),
                str(len(doors)), str(matched), str(len(manual_x) - matched),
            )
    console.print(table)
    console.print(
        "промахи — двери, которые человек разметил, а метод не нашёл; "
        "разница «предложено минус совпало» — ложные срабатывания"
    )


@doors_app.command("jitter")
def doors_jitter(target: Path = typer.Argument(VIDEO_DIR)) -> None:
    """Дрожание дверной зоны внутри визита — на стационарных визитах обязано
    быть 0px в счёте. Ловит рассинхрон системы координат сразу, без эталона.
    """
    from .diagnose import zone_jitter
    from .visits import build_scene

    failed = False
    for video in videos_in(target):
        data, _, _ = get_tracks(video)
        cfg = load_config(video)
        if not cfg.doors:
            continue
        console.print(f"\n[bold]{video.name}[/bold]")
        scene = build_scene(data, cfg)
        failed = zone_jitter(scene, cfg, console) or failed
    if failed:
        raise typer.Exit(1)


@doors_app.command("remap")
def doors_remap(video: Path = typer.Argument(...)) -> None:
    """Пересчитывает доли ручной разметки под новый канонический bbox так,
    чтобы сохранился пиксельный прямоугольник, зафиксированный в reference_box.

    Нужна после смены конвенции стабилизации bbox (см. плановый документ):
    без ремапа старая разметка молча указывает не туда.
    """
    from .core.types import Point
    from .visits import build_scene

    cfg = load_config(video)
    if cfg.reference_box is None or cfg.reference_frame is None:
        raise typer.BadParameter(
            f"{video.name}: нет reference_box/reference_frame — нечего ремапить "
            "(разметка либо не ручная, либо снята до введения reference_box)"
        )
    data, _, _ = get_tracks(video)
    scene = build_scene(data, cfg)
    new_box = scene.boxes.get(cfg.reference_frame)
    if new_box is None:
        raise typer.BadParameter(
            f"{video.name}: на reference_frame={cfg.reference_frame} нет bbox ТС в новой схеме"
        )
    old_box = tuple(float(v) for v in cfg.reference_box)
    new_box_t = tuple(float(v) for v in new_box)

    def to_frac(x: float, y: float, box: tuple[float, float, float, float]) -> tuple[float, float]:
        x0, y0, x1, y1 = box
        return (x - x0) / (x1 - x0), (y - y0) / (y1 - y0)

    changed = 0
    for d in cfg.doors:
        if d.frame != "vehicle":
            console.print(f"  [dim]{d.door_id}: frame={d.frame}, пропущено[/dim]")
            continue
        zx0, zy0, zx1, zy1 = d.resolve_zone(old_box)
        a, b = d.resolve(old_box)
        nx0, ny0 = to_frac(zx0, zy0, new_box_t)
        nx1, ny1 = to_frac(zx1, zy1, new_box_t)
        d.zone = (round(nx0, 4), round(ny0, 4), round(nx1, 4), round(ny1, 4))
        lsx, lsy = to_frac(a.x, a.y, new_box_t)
        lex, ley = to_frac(b.x, b.y, new_box_t)
        d.line_start = Point(x=round(lsx, 4), y=round(lsy, 4))
        d.line_end = Point(x=round(lex, 4), y=round(ley, 4))
        changed += 1
    cfg.reference_box = tuple(round(v, 2) for v in new_box_t)
    path = save_config(cfg)
    console.print(f"[green]✓[/green] {video.name}: перенесено дверей {changed}, записано в {path}")


def _iou_1d(a: tuple[float, float], b: tuple[float, float]) -> float:
    inter = max(0.0, min(a[1], b[1]) - max(a[0], b[0]))
    union = (a[1] - a[0]) + (b[1] - b[0]) - inter
    return inter / union if union > 0 else 0.0


@app.command()
def serve(
    host: str = typer.Option("127.0.0.1", help="Только петля: наружу не публикуем."),
    port: int = typer.Option(8000),
) -> None:
    """Поднимает локальный веб-UI: разметка дверей, запуск, просмотр."""
    import uvicorn

    log.info("веб-UI на http://%s:%d", host, port)
    uvicorn.run("paxcount.webui.app:app", host=host, port=port, reload=False)


if __name__ == "__main__":
    app()
