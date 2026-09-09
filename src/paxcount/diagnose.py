"""Диагностика по кэшу треков: почему получилось именно столько.

Считает не картинку, а числа, по которым видно, где ломается логика:
какой трек выбран главным, как ведёт себя его скорость, на какие визиты
разрезано видео и какие треки людей вообще подходили к линии двери.
"""

from __future__ import annotations

import numpy as np
from rich.console import Console
from rich.table import Table

from .core.geometry import anchor_of, distance_to_segment, side_of_line
from .core.trackdata import TrackData
from .core.types import VideoConfig
from .visits import Scene, _normalised_speed, _size_ratio, _smooth, build_scene, vehicle_tracks
from .zonecount import activity_window, classify, collect_lives, edge_guard_seconds


def diagnose(
    data: TrackData, config: VideoConfig, console: Console, door: bool = False
) -> None:
    fps = data.effective_fps
    tracks = vehicle_tracks(data)
    scene = build_scene(data, config)
    primary = scene.primary

    t = Table(title="Треки ТС")
    for c in ("id", "класс", "кадров", "сумм. площадь", "ср. площадь", "главный"):
        t.add_column(c)
    for tid, tr in sorted(tracks.items(), key=lambda kv: -len(kv[1].frames))[:8]:
        arr = np.asarray(tr.boxes, dtype=float)
        areas = (arr[:, 2] - arr[:, 0]) * (arr[:, 3] - arr[:, 1])
        name = next(
            (n for f in data.frames for i, n in zip(f.vehicle_ids, f.vehicle_names)
             if int(i) == tid), "?"
        )
        t.add_row(
            str(tid), name, str(len(tr.frames)), f"{areas.sum():.3e}",
            f"{areas.mean():.0f}", "★" if primary and tid == primary.track_id else "",
        )
    console.print(t)

    if primary is None:
        console.print("[red]ТС не найдено[/red]")
        return

    speed = _smooth(_normalised_speed(primary.boxes), max(1, int(fps * 0.5)))
    console.print(
        f"скорость главного ТС: медиана {np.median(speed):.4f}, "
        f"p90 {np.percentile(speed, 90):.4f}, макс {speed.max():.4f}, "
        f"порог {config.stationary_speed}"
    )
    below = (speed < config.stationary_speed).mean()
    console.print(f"кадров ниже порога: {below:.0%}")

    visits = scene.visits
    vt = Table(title=f"Визиты ({len(visits)})")
    for c in ("id", "с", "по", "длительность", "устойч. размер (p90/p10)", "стоянка?"):
        vt.add_column(c)
    for v in visits:
        idx = [i for i, t in enumerate(primary.times) if v.arrival_ts <= t <= v.departure_ts]
        ratio = _size_ratio([primary.boxes[i] for i in idx]) if len(idx) >= 2 else float("nan")
        vt.add_row(
            str(v.visit_id), f"{v.arrival_ts:.1f}", f"{v.departure_ts:.1f}",
            f"{v.departure_ts - v.arrival_ts:.1f} с", f"{ratio:.2f}",
            "да" if v.stationary else "[yellow]нет — движение[/yellow]",
        )
    console.print(vt)

    boxes = scene.boxes
    spec = config.doors[0]
    rows: dict[int, dict] = {}
    for f in data.frames:
        bbox = boxes.get(f.frame_idx)
        if bbox is None:
            continue
        a, b = spec.resolve(tuple(float(v) for v in bbox))
        band = max(spec.band * float(bbox[3] - bbox[1]), 1.0)
        for tid, pbox in zip(f.person_ids, f.person_boxes):
            anchor = anchor_of(pbox)
            dist = distance_to_segment(anchor, a, b)
            side = side_of_line(anchor, a, b)
            r = rows.setdefault(
                int(tid),
                {"frames": 0, "min_dist": 1e9, "sides": set(), "in_band": 0,
                 "first": f.ts, "last": f.ts},
            )
            r["frames"] += 1
            r["min_dist"] = min(r["min_dist"], dist)
            r["sides"].add(side)
            r["in_band"] += dist <= band
            r["last"] = f.ts

    pt = Table(title="Треки людей у линии (топ по близости)")
    for c in ("id", "кадров", "в полосе", "мин. дистанция", "стороны", "с", "по"):
        pt.add_column(c)
    for tid, r in sorted(rows.items(), key=lambda kv: kv[1]["min_dist"])[:14]:
        pt.add_row(
            str(tid), str(r["frames"]), str(r["in_band"]), f"{r['min_dist']:.0f}",
            ",".join(str(s) for s in sorted(r["sides"])),
            f"{r['first']:.1f}", f"{r['last']:.1f}",
        )
    console.print(pt)
    console.print(
        f"треков людей всего: {len(rows)}; "
        f"сменили сторону: {sum(1 for r in rows.values() if len(r['sides'] - {0}) > 1)}"
    )

    if door:
        zone_jitter(scene, config, console)
        _door_diagnostics(data, config, console, visits, boxes)


def zone_jitter(scene: Scene, config: VideoConfig, console: Console) -> bool:
    """Дрожание дверной зоны внутри визита — на сыром bbox и на bbox счёта.

    Это метрика, которая поймала бы дефект сразу: она сравнивает шум системы
    координат (насколько разъезжается зона) с размером самой зоны. На
    стационарном визите дрожание в счёте обязано быть 0px по построению —
    иначе где-то в пайплайн просочился сырой bbox мимо `build_scene`.

    Возвращает True, если хоть одна проверка провалена (годится как код
    возврата для CI).
    """
    zone_specs = [d for d in config.doors if d.mode == "zone"]
    if not zone_specs or scene.primary is None:
        console.print("[dim]дверей в режиме zone нет — дрожание неприменимо[/dim]")
        return False

    jt = Table(title="Дрожание дверной зоны (размах края / ширина зоны)")
    for c in ("визит", "дверь", "ширина зоны", "сырое дрожание", "дрожание в счёте", "вердикт"):
        jt.add_column(c)

    failed = False
    for v in scene.visits:
        # Рамки берутся у ТС этого визита, а не у главного ТС сцены: визитов
        # на ролике может быть много и от разных машин.
        raw_by_frame = scene.raw_for(v)
        counted = scene.boxes_for(v)
        frames = sorted(raw_by_frame)
        raw = [raw_by_frame[f] for f in frames]

        # Внутри стационарного визита bbox счёта меняется по окнам (см.
        # canonical_windows в visits.py) — это НАМЕРЕННОЕ поведение, а не
        # дрожание: каждое окно получает свой канонический bbox, потому что
        # ТС может реально покачиваться на протяжении долгой стоянки. Поэтому
        # дрожание меряется ВНУТРИ каждого окна отдельно (там оно обязано
        # быть 0 по построению — все кадры окна используют один и тот же
        # объект bbox), а не по всему визиту сразу.
        by_window: dict[int, list] = {}
        for f in frames:
            b = counted.get(f)
            if b is not None:
                by_window.setdefault(id(b), []).append(b)

        for spec in zone_specs:
            raw_zones = np.asarray([spec.resolve_zone(tuple(float(x) for x in b)) for b in raw])
            width = float(np.median(raw_zones[:, 2] - raw_zones[:, 0])) if len(raw_zones) else 0.0
            raw_span = float(np.max(raw_zones.max(axis=0) - raw_zones.min(axis=0))) if len(raw_zones) else 0.0

            if not v.stationary:
                jt.add_row(str(v.visit_id), spec.door_id, f"{width:.0f}", f"{raw_span:.0f}px",
                           "-", "[dim]не стабилизируется (визит нестационарный)[/dim]")
                continue

            worst = 0.0
            for group in by_window.values():
                zones = np.asarray([spec.resolve_zone(tuple(float(x) for x in b)) for b in group])
                worst = max(worst, float(np.max(zones.max(axis=0) - zones.min(axis=0))) if len(zones) else 0.0)
            ok = worst <= 1.0
            failed = failed or not ok
            raw_pct = f"{100 * raw_span / width:.0f}%" if width else "-"
            verdict = (
                f"[green]ок[/green] ({len(by_window)} окон)" if ok
                else f"[red]сломано: {worst:.1f}px внутри окна[/red]"
            )
            jt.add_row(
                str(v.visit_id), spec.door_id, f"{width:.0f}",
                f"{raw_span:.0f}px ({raw_pct})", f"{worst:.1f}px", verdict,
            )
    console.print(jt)
    return failed


def _door_diagnostics(data, config, console, visits, boxes) -> None:
    """Опережающие метрики без эталона: фрагментация треков и причины
    отказа classify() — работает на любом видео, включая боевое неразмеченное.
    """
    # data сюда приходит уже сшитой (get_tracks сшивает при загрузке), поэтому
    # build_remap здесь всегда покажет 0 — это ожидаемо, идемпотентность, а не
    # баг. Цифры ниже — фрагментация ПОСЛЕ сшивки, то есть то, что реально
    # дойдёт до счёта.
    all_ids: dict[int, int] = {}
    for f in data.frames:
        for tid in f.person_ids:
            all_ids[int(tid)] = all_ids.get(int(tid), 0) + 1
    short_after = sum(1 for n in all_ids.values() if n < 8)
    console.print(
        f"\n[bold]фрагментация (после сшивки в get_tracks):[/bold] "
        f"треков людей {len(all_ids)}, короче 8 кадров {short_after} "
        f"({short_after / max(len(all_ids), 1):.0%})"
    )

    zone_specs = [d for d in config.doors if d.mode == "zone"]
    if not zone_specs:
        console.print("[dim]дверей в режиме zone нет — гистограмма classify() неприменима[/dim]")
        return

    ft = Table(title="Причины отказа classify() по всем визитам (режим zone)")
    for c in ("причина", "треков"):
        ft.add_column(c)
    from collections import Counter

    reasons: Counter[str] = Counter()
    for v in visits:
        lo, hi = activity_window(data, zone_specs, v, boxes)
        window = v.model_copy(update={"arrival_ts": lo, "departure_ts": hi})
        lives = collect_lives(data, zone_specs, window, boxes)
        guard = edge_guard_seconds(window)
        for life in lives.values():
            _, why = classify(life, window, data.width, data.height, 0.5, guard)
            reasons[why] += 1
    for reason, n in reasons.most_common():
        ft.add_row(reason, str(n))
    console.print(ft)
