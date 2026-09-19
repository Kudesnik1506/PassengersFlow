"""Кадры для счёта дверей и сведение ответов в коды таблицы 2.

    uv run python tools/edgedoors.py export --stop 22739 [--limit 15]
    uv run python tools/edgedoors.py apply  --stop 22739

Зачем. Код 5-8 говорит «посчитать нельзя, дверь за кадром». Геометрия этого не
знает: она видит только, что кузов упёрся в край кадра, а сколько за тем краем
осталось — нет. Замер: из 239 обрезанных стоянок у 155 за кадром меньше 5 %
кузова, то есть все двери на месте. Отсюда счёт дверей по кадру (решение 082).

Что видит модель. Один кадр и рамку машины на нём. Ни размера ТС, ни числа
дверей по таблице 3, ни направления движения, ни наших прежних ответов: счётчик
слепой по той же причине, что и в `llm_count.py` — ответ с оглядкой на эталон
это подгонка, а не замер.

Направление у модели не спрашивается вовсе. Задано заказчиком (18.09): нос
всегда справа. В пилоте модель назвала обрезанный конец «передним» на трёх
кадрах подряд, в том числе на двух с противоположными обрезами.

Кадры ложатся только под `out/` — проверяется, а не доверяется (запрет 6).
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from collections import defaultdict
from datetime import datetime
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

import cv2  # noqa: E402
from rich.console import Console  # noqa: E402

from paxcount import cameras  # noqa: E402
from paxcount.counting.export import refuse_outside  # noqa: E402
from paxcount.delivery.edgedoors import (  # noqa: E402
    DoorAnswer, agreed, code_from_answer, cut_end,
)
from paxcount.delivery.timeline import parse_slot  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR, videos_in  # noqa: E402

console = Console(width=140)

ROOT = OUT_DIR / "edge-doors"
FRAMES_DIR = ROOT / "кадры"
TASK = ROOT / "задание.json"
ANSWERS_DIR = ROOT / "ответы"
CODES = ROOT / "коды.csv"
CODE_FIELDS = ("camera", "visit_start", "дверей_всего", "попало_в_кадр",
                "конец", "код", "причина")


def sightings_of(stop: str) -> list[dict]:
    """Стоянки из детекции — как их посчитал `tools/sightings.py`."""
    folder = OUT_DIR / "sightings"
    out: list[dict] = []
    for path in sorted(folder.glob(f"{stop}-*.csv")):
        with path.open(encoding="utf-8", newline="") as f:
            out.extend(csv.DictReader(f))
    return out


def clipped(stop: str) -> tuple[list[dict], dict[str, int]]:
    """Стоянки с обрезанным кузовом и счёт того, почему остальные отсеяны.

    Кадр нарезается только там, где кузов задел край: если машина влезла
    целиком, кода нет по построению и спрашивать модель не о чем.
    """
    motions = cameras.load(cameras.path_for(stop, DATA_DIR / "cameras"))
    noses = {}
    tasks, skipped = [], defaultdict(int)
    for row in sightings_of(stop):
        camera = row["camera"]
        if camera not in noses:
            noses[camera] = cameras.nose_for(stop, camera, motions)
        box = (float(row["x0"]), float(row["y0"]),
                float(row["x1"]), float(row["y1"]))
        size = (int(row["width"]), int(row["height"]))
        end, why = cut_end(box, size, noses[camera])
        if end is None:
            skipped["кузов в кадре целиком" if why is None else why] += 1
            continue
        tasks.append({**row, "конец": end, "рамка": box, "кадр": size})
    return tasks, dict(skipped)


def grab(video: Path, second: float):
    """Один кадр на названной секунде файла. Перемотка, а не чтение подряд.

    Кадров нужно по одному на стоянку из сорока восьми файлов: читать каждый
    файл подряд значит потратить часы там, где хватает перемотки.
    """
    cap = cv2.VideoCapture(str(video))
    if not cap.isOpened():
        raise RuntimeError(f"не открывается видео: {video}")
    try:
        fps = cap.get(cv2.CAP_PROP_FPS) or 25.0
        cap.set(cv2.CAP_PROP_POS_FRAMES, max(0, int(second * fps)))
        ok, frame = cap.read()
        return frame if ok else None
    finally:
        cap.release()


def export(stop: str, limit: int | None, camera: str | None) -> int:
    refuse_outside(FRAMES_DIR, OUT_DIR)
    tasks, skipped = clipped(stop)
    if camera:
        tasks = [t for t in tasks if t["camera"] == camera]
    tasks.sort(key=lambda t: (t["start"], t["camera"]))
    if limit:
        tasks = tasks[:limit]

    videos = {p.stem: p for p in videos_in(None)}
    FRAMES_DIR.mkdir(parents=True, exist_ok=True)
    written = []
    for task in tasks:
        video = videos.get(task["file"])
        if video is None:
            console.print(f"[yellow]нет файла {task['file']}[/yellow]")
            continue
        slot = parse_slot(task["file"])
        start = datetime.fromisoformat(task["start"])
        end = datetime.fromisoformat(task["end"])
        middle = ((start - slot.start).total_seconds()
                   + (end - start).total_seconds() / 2)
        frame = grab(video, middle)
        if frame is None:
            console.print(f"[yellow]кадр не прочитан: {task['file']} {middle:.1f} с[/yellow]")
            continue
        name = f"К{task['camera']}-{start:%H-%M-%S}.jpg"
        cv2.imwrite(str(FRAMES_DIR / name), frame)
        written.append({
            "кадр": name,
            "camera": task["camera"],
            "visit_start": task["start"],
            "размер_кадра": list(task["кадр"]),
            "рамка_ТС": [round(v, 1) for v in task["рамка"]],
            # Срезанный конец в задание пишется, но счётчику не показывается:
            # он нужен на сведении, чтобы понять, какие номера дверей пропали.
            "конец": task["конец"],
        })

    TASK.write_text(json.dumps(written, ensure_ascii=False, indent=1),
                     encoding="utf-8")
    console.print(f"кадров: {len(written)} → {FRAMES_DIR}")
    for why, n in sorted(skipped.items(), key=lambda kv: -kv[1]):
        console.print(f"  [dim]без кадра {n}: {why}[/dim]")
    return 0


def read_answers() -> dict[str, list[DoorAnswer]]:
    """Ответы прогонов по кадрам. Прогон — отдельный файл в `ответы/`."""
    by_frame: dict[str, list[DoorAnswer]] = defaultdict(list)
    if not ANSWERS_DIR.is_dir():
        return by_frame
    for path in sorted(ANSWERS_DIR.glob("*.json")):
        for frame, answer in json.loads(path.read_text(encoding="utf-8")).items():
            by_frame[frame].append(DoorAnswer(
                total=int(answer["дверей_всего"]),
                in_frame=int(answer["попало_в_кадр"]),
            ))
    return by_frame


def apply(stop: str) -> int:
    tasks = json.loads(TASK.read_text(encoding="utf-8"))
    answers = read_answers()
    if not answers:
        console.print("[red]ответов нет — сначала прогоны[/red]")
        return 1

    rows, coded, silent = [], 0, 0
    for task in tasks:
        runs = answers.get(task["кадр"], [])
        answer, why = agreed(runs)
        if answer is None:
            rows.append({"camera": task["camera"], "visit_start": task["visit_start"],
                          "дверей_всего": "", "попало_в_кадр": "",
                          "конец": task["конец"], "код": "", "причина": why})
            silent += 1
            continue
        code, note = code_from_answer(answer, task["конец"])
        rows.append({"camera": task["camera"], "visit_start": task["visit_start"],
                      "дверей_всего": answer.total, "попало_в_кадр": answer.in_frame,
                      "конец": task["конец"], "код": code or "",
                      "причина": note or ""})
        coded += code is not None
        silent += code is None

    CODES.parent.mkdir(parents=True, exist_ok=True)
    with CODES.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(CODE_FIELDS))
        writer.writeheader()
        writer.writerows(rows)
    console.print(f"стоянок {len(rows)}: с кодом {coded}, без кода {silent} → {CODES}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("export", "apply"))
    parser.add_argument("--stop", default="22739")
    parser.add_argument("--limit", type=int, default=None,
                         help="сколько стоянок нарезать — для проверки на глаз")
    parser.add_argument("--camera", default=None)
    args = parser.parse_args()
    if args.action == "export":
        return export(args.stop, args.limit, args.camera)
    return apply(args.stop)


if __name__ == "__main__":
    raise SystemExit(main())
