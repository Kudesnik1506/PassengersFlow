#!/usr/bin/env python3
"""Гейт разметки дверей — та часть `paxcount check`, которой не нужны данные.

Зачем отдельно: `paxcount check` требует видео и окружения проекта (torch,
ultralytics), а на сервере ни того, ни другого нет — кадры не версионируются.
Но именно ошибки разметки оказались самым дорогим классом дефектов: они тихие,
прогон не падает, счёт просто становится неверным. Проверить их можно по одному
JSON, без единого кадра, — что здесь и делается.

Пороги не дублируются, а берутся из `paxcount.settings`: он зависит только от
стандартной библиотеки, поэтому импортируется без установки проекта.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "src"))

from paxcount.settings import (  # noqa: E402
    ZONE_FRACTION_MAX,
    ZONE_FRACTION_MIN,
    ZONE_MIN_BOTTOM,
    ZONE_MIN_TOP,
)


def check_config(path: Path) -> list[str]:
    problems: list[str] = []
    try:
        cfg = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{path.name}: JSON не парсится — {exc.msg} (строка {exc.lineno})"]

    if not isinstance(cfg.get("video"), str) or not cfg["video"]:
        problems.append(f"{path.name}: нет поля video")

    doors = list(cfg.get("doors") or [])
    for ds in cfg.get("door_sets") or []:
        doors += list(ds.get("doors") or [])

    for d in doors:
        did = d.get("door_id", "?")
        if d.get("mode") != "zone":
            continue
        zone = d.get("zone")
        if not (isinstance(zone, list) and len(zone) == 4):
            problems.append(f"{path.name}: дверь {did} — zone не из четырёх чисел")
            continue
        x0, y0, x1, y1 = zone
        if not (x0 < x1 and y0 < y1):
            problems.append(f"{path.name}: дверь {did} — zone не x0<x1, y0<y1")
        if any(v < ZONE_FRACTION_MIN or v > ZONE_FRACTION_MAX for v in zone):
            problems.append(
                f"{path.name}: дверь {did} — доля zone вне "
                f"[{ZONE_FRACTION_MIN}, {ZONE_FRACTION_MAX}] ({zone}), похоже на опечатку"
            )
        if y0 < ZONE_MIN_TOP:
            problems.append(
                f"{path.name}: дверь {did} — верх зоны {y0:.2f} выше {ZONE_MIN_TOP}: "
                "зона захватывает салон и будет ловить пассажиров, видимых сквозь дверь"
            )
        if y1 < ZONE_MIN_BOTTOM:
            problems.append(
                f"{path.name}: дверь {did} — низ зоны {y1:.2f} выше {ZONE_MIN_BOTTOM}: "
                "зона обрывается над землёй и не поймает ноги стоящего у двери человека"
            )
    return problems


def main() -> int:
    zones = sorted(p for p in (ROOT / "data" / "zones").glob("*.json") if ".auto" not in p.name)
    problems: list[str] = []
    for path in zones:
        problems += check_config(path)

    if problems:
        print("✗ гейт разметки не пройден:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"✓ разметка: {len(zones)} конфигов, замечаний нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
