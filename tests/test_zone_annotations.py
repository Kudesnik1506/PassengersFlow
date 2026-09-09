"""Разметка проверяется тем же кодом, что и в pre-push (принцип 5).

Логика гейта здесь не пересказывается: тест зовёт `gate_zones.check_config`.
Пересказ означал бы две реализации одного правила, которые разойдутся — и тогда
зелёный тест перестанет что-либо гарантировать о том, что реально блокирует
push.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "hooks"))

import gate_zones  # noqa: E402

ZONE_FILES = sorted(p for p in (ROOT / "data" / "zones").glob("*.json") if ".auto" not in p.name)


@pytest.mark.parametrize("path", ZONE_FILES, ids=lambda p: p.stem)
def test_real_annotation_passes_the_gate(path: Path):
    assert gate_zones.check_config(path) == []


def test_annotations_are_actually_present():
    """Пустой набор прошёл бы предыдущий тест молча, ничего не проверив."""
    assert ZONE_FILES


def write(tmp_path: Path, doors: list[dict]) -> Path:
    path = tmp_path / "probe.json"
    path.write_text(json.dumps({"video": "probe.mp4", "doors": doors}), encoding="utf-8")
    return path


def door(zone: list[float]) -> dict:
    return {"door_id": "door1", "mode": "zone", "zone": zone}


def test_gate_rejects_a_zone_ending_above_the_ground(tmp_path):
    """Та самая ошибка с 09: зона обрывается над землёй."""
    problems = gate_zones.check_config(write(tmp_path, [door([0.4, 0.65, 0.6, 0.85])]))
    assert any("не поймает ноги" in p for p in problems)


def test_gate_rejects_a_zone_reaching_into_the_saloon(tmp_path):
    problems = gate_zones.check_config(write(tmp_path, [door([0.4, 0.20, 0.6, 1.12])]))
    assert any("захватывает салон" in p for p in problems)


def test_gate_rejects_inverted_zone(tmp_path):
    problems = gate_zones.check_config(write(tmp_path, [door([0.6, 0.65, 0.4, 1.12])]))
    assert any("x0<x1" in p for p in problems)


def test_gate_checks_doors_inside_door_sets(tmp_path):
    """Двери внутри `door_sets` — та же разметка и те же требования.

    Набор дверей по соотношению сторон появился для многочасовых записей; если
    гейт заглядывает только в верхнеуровневый `doors`, для длинного видео
    проверки нет вообще.
    """
    path = tmp_path / "probe.json"
    path.write_text(
        json.dumps({
            "video": "probe.mp4",
            "doors": [],
            "door_sets": [{
                "name": "автобус",
                "aspect_min": 1.0,
                "aspect_max": 3.0,
                "doors": [door([0.4, 0.65, 0.6, 0.85])],
            }],
        }),
        encoding="utf-8",
    )
    assert any("не поймает ноги" in p for p in gate_zones.check_config(path))
