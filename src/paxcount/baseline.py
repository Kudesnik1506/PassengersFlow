"""Замороженный baseline: числа по видео на момент последнего согласованного
состояния, чтобы следующая правка показывала дельту, а не голый абсолют.

Без этого файла проверка «ничего не сломал» держится на памяти того, кто
правил код — а через неделю правок таких чисел уже пять и все в голове не
держатся.
"""

from __future__ import annotations

import json
from pathlib import Path

from rich.console import Console
from rich.table import Table

from .backends.custom import CustomBackend
from .doors import load_config
from .settings import DATA_DIR, videos_in
from .tracking import get_tracks

BASELINE_PATH = DATA_DIR / "truth" / "baseline.json"


def compute_baseline(target: Path) -> dict[str, dict[str, int]]:
    out: dict[str, dict[str, int]] = {}
    for video in videos_in(target):
        data, _, _ = get_tracks(video)
        cfg = load_config(video)
        result = CustomBackend().run(data, cfg)
        out[video.name] = {"boarded": result.boarded, "alighted": result.alighted}
    return out


def save_baseline(baseline: dict[str, dict[str, int]], path: Path = BASELINE_PATH) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(baseline, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load_baseline(path: Path = BASELINE_PATH) -> dict[str, dict[str, int]]:
    if not path.exists():
        raise FileNotFoundError(f"нет baseline: {path}. Создайте: paxcount baseline --save")
    return json.loads(path.read_text(encoding="utf-8"))


def diff_baseline(
    old: dict[str, dict[str, int]], new: dict[str, dict[str, int]], console: Console
) -> bool:
    """Печатает дельту по видео. Возвращает True, если есть расхождения."""
    table = Table(title="Дельта против замороженного baseline")
    for col in ("видео", "вошло было", "вошло стало", "вышло было", "вышло стало", ""):
        table.add_column(col)
    changed = False
    videos = sorted(set(old) | set(new))
    for video in videos:
        o = old.get(video, {"boarded": "—", "alighted": "—"})
        n = new.get(video, {"boarded": "—", "alighted": "—"})
        diff = o != n
        changed = changed or diff
        table.add_row(
            video, str(o["boarded"]), str(n["boarded"]), str(o["alighted"]), str(n["alighted"]),
            "[yellow]изменилось[/yellow]" if diff else "",
        )
    console.print(table)
    return changed
