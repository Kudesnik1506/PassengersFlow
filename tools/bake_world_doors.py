"""Разовое запекание классов YOLOWorld в веса — офлайн, один раз.

``YOLOWorld.set_classes()`` тянет CLIP при первом вызове. Прогонять его в
рантайме на каждом запуске значит либо держать CLIP зависимостью прод-сервиса
целиком, либо (что и делает ultralytics по умолчанию) молча ставить его из
git при первом обращении — нарушение принципа цепочки поставки.

Правильно — запечь текстовый энкодер в веса один раз здесь, где сеть и CLIP
уже есть по явному решению (см. pyproject.toml), и дальше в рантайме
загружать готовый ``models/yolov8s-worldv2-doors.pt`` без CLIP вовсе.

Запуск: ``uv run python tools/bake_world_doors.py``
"""

from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from ultralytics import YOLOWorld  # noqa: E402

from paxcount.doorprop.openvocab import ALL_PROMPTS, BAKED_WEIGHTS, SOURCE_WEIGHTS  # noqa: E402


def main() -> None:
    BAKED_WEIGHTS.parent.mkdir(parents=True, exist_ok=True)
    print(f"базовые веса: {SOURCE_WEIGHTS}")
    model = YOLOWorld(SOURCE_WEIGHTS)
    print(f"запекаю {len(ALL_PROMPTS)} промптов: {ALL_PROMPTS}")
    model.set_classes(ALL_PROMPTS)
    model.save(str(BAKED_WEIGHTS))
    print(f"готово: {BAKED_WEIGHTS}")
    print("рантайм больше не обращается к CLIP — классы уже в весах.")


if __name__ == "__main__":
    main()
