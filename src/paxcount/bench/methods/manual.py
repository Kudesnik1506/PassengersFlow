"""Указание дверей мультимодальной моделью — ответ приходит файлом, не по сети.

Владелец работает по подписке, а не по API: ключа у проекта нет, и запросить
модель из кода нельзя. Но сам подход от этого не отпадает — он просто
выполняется в сессии: кадр и рамка кузова показываются модели прямо в
переписке, а координаты проёмов кладутся сюда файлом.

Это честнее, чем кажется. Метод всё равно сравнивается по тем же кадрам, той
же рамке и той же мере, что остальные. Разница только в канале доставки — и
она записана, а не спрятана: строка таблицы прямо говорит, что ответ получен
вручную, поэтому сравнивать его по времени работы с автоматическими методами
нельзя, а по попаданиям — можно.

Формат файла `data/bench/vlm/<ключ визита>.json`:

    {"boxes": [[x0, y0, x1, y1], ...], "note": "чем отвечала модель"}

Координаты — пиксели КАДРА, как и у всех прочих методов.
"""

from __future__ import annotations

import json

from ...settings import DATA_DIR
from ..cases import Case
from ..finders import Box, FrameSource

ANSWERS_DIR = DATA_DIR / "bench" / "vlm"


def answer_path(visit_key: str):
    slug = visit_key.replace("/", "_").replace(":", "-").strip("_")
    return ANSWERS_DIR / f"{slug}.json"


class VlmFinder:
    """Ответ мультимодальной модели, полученный в сессии и записанный файлом."""

    tag = "vlm"
    title = "указание моделью (ответ файлом)"

    def __init__(self) -> None:
        self.note = ""

    def available(self) -> str | None:
        if not ANSWERS_DIR.is_dir():
            return (f"нет ответов модели: каталог {ANSWERS_DIR} пуст — "
                    "кадры показываются модели в сессии, координаты кладутся файлом")
        return None

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        path = answer_path(case.visit_key)
        if not path.exists():
            raise FileNotFoundError(
                f"нет ответа модели для визита {case.visit_key}: ожидается {path}"
            )
        data = json.loads(path.read_text(encoding="utf-8"))
        self.note = "ответ получен в сессии, " + str(data.get("note", ""))
        return [tuple(float(v) for v in box) for box in data["boxes"]]
