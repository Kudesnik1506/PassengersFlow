"""Карта покрытия смены: где каждая камера реально не писала.

Печатает разрывы записи (`paxcount.delivery.coverage`) на каждой камере
отдельно — в её СОБСТВЕННЫХ, необработанных часах. Для К2 (точка отсчёта
шкалы, см. `delivery.clocks`) это уже и есть общее время без пересчёта; для К1
и К3 поправка из `data/clocks/<остановка>.csv` пока черновая (разброс больше
порога готовности), поэтому здесь она не применяется — разница между камерами
на глаз всё равно видна, офсеты в этом окне минуты, а не часы.

Если для остановки есть выгрузка оператора, в отчёт попадает и число его
записей внутри каждого разрыва — это и есть цена дыры, а не только её длина
(план, шаг 0б).

Логика гейта (что считать разрывом, что писать в строку) — в
`paxcount.delivery.coverage`, покрыта тестами без ffprobe и без боевых видео.
Здесь — только доступ к файлам и внешней команде.

Запуск:
    uv run python tools/coverage.py "data/prod_videos/видео 1" 22739 \\
        [--export "data/prod_videos/видео 1/2026-09-14_..._na_ostanovkakh.xlsx"]
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from paxcount.delivery.coverage import gaps_for, render_report  # noqa: E402
from paxcount.delivery.timeline import sessions_from_names  # noqa: E402
from paxcount.settings import VIDEO_SUFFIXES  # noqa: E402

MIN_GAP_S = 5.0


def probe_duration_s(path: Path) -> float:
    """Настоящая длительность записи файла, секунды. Требует ffprobe в PATH."""
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    )
    return float(result.stdout.strip())


def build_sessions(root: Path, stop: str):
    """Собирает по одной сессии на камеру с настоящей длительностью файлов."""
    from paxcount.delivery.timeline import parse_slot

    paths = [p for p in root.rglob("*") if p.suffix.lower() in VIDEO_SUFFIXES]
    names, duration = [], {}
    for path in paths:
        try:
            slot = parse_slot(path.stem)
        except ValueError:
            continue
        if slot.stop != stop:
            continue
        names.append(path.stem)
        duration[path.stem] = probe_duration_s(path)
    return sessions_from_names(names, duration_of=duration.get)


def operator_counter(export_path: Path | None, stop: str):
    """Возвращает функцию (начало, конец) -> число записей оператора внутри.

    Без выгрузки считает 0 везде — отчёт остаётся честным про то, чего у него
    нет, а не падает.
    """
    if export_path is None or not export_path.exists():
        return lambda a, b: 0

    from paxcount.delivery.operator import for_stop, read_export

    records = for_stop(read_export(export_path), stop)

    def count(start, end) -> int:
        return sum(1 for r in records if start <= r.created <= end)

    return count


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print(__doc__)
        return 2
    root = Path(argv[0])
    stop = argv[1]
    export = None
    if "--export" in argv:
        export = Path(argv[argv.index("--export") + 1])

    counter = operator_counter(export, stop)
    sessions = build_sessions(root, stop)
    if not sessions:
        print(f"видео остановки {stop} не найдено под {root}")
        return 1

    for session in sorted(sessions, key=lambda s: (s.camera, s.start)):
        gaps = gaps_for(session.camera or "?", session, MIN_GAP_S, counter)
        label = f"камера {session.camera or '?'}, смена от {session.start:%Y-%m-%d %H:%M:%S}"
        print(f"--- {label} ({session.duration_s:.0f} с) ---")
        print(render_report(gaps))
    if any(gaps_for(s.camera or "?", s, MIN_GAP_S, counter) for s in sessions) and export:
        print(
            "\nВНИМАНИЕ: время дыр — по часам самой камеры, без поправки из "
            "data/clocks/22739.csv. Для К2 (точка отсчёта шкалы) это не проблема "
            "— её часы и есть общее время. Для К1 и К3 таблица пока несёт "
            "черновые значения (решение 033: разброс больше порога готовности), "
            "здесь они не применяются. Число записей оператора внутри дыры "
            "сверялось с тем же необработанным временем камеры и на К1/К3 может "
            "уйти на величину поправки (минуты, не секунды): это оценка, а не "
            "точный счёт."
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
