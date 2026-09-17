"""Прогон сравнения: недоступный метод и упавший метод — не одно и то же.

На выборке в пять машин таблица результатов читается целиком, и каждая
нехватка в ней обязана быть названа. Поэтому у прогона три исхода на метод, а
не два: отработал, не установлен, упал. Метод, которого нет на машине, не
должен ни ронять прогон, ни тихо получать ноль — ноль означал бы «метод не
нашёл ни одной двери», а это неправда.

Методы не импортируются при импорте реестра: в реестре лежат фабрики. Иначе
`import bench.methods` тянул бы torch, transformers и detectron2 разом — и
проверить сам прогон стало бы нельзя без всех трёх.
"""

from __future__ import annotations

import numpy as np
import pytest
from paxcount.bench.cases import Case
from paxcount.bench.run import run_case
from paxcount.delivery.model import VehicleSize

FRAME = (1920, 1080)
BODY = (300.0, 400.0, 1500.0, 800.0)
DOORS = ((400.0, 500.0, 480.0, 780.0), (800.0, 500.0, 880.0, 780.0))


def case() -> Case:
    return Case(
        visit_key="2/2026-09-10T07:04:01/38157", camera="2",
        video="видео", frame_idx=10, frame_size=FRAME, body_px=BODY,
        size=VehicleSize.MEDIUM, orientation="нос-слева",
        doors_visible=DOORS, doors_total=2,
    )


class Fake:
    """Метод-заглушка: отдаёт заданное или ведёт себя заданным образом."""

    def __init__(self, tag, boxes=None, reason=None, boom=None):
        self.tag, self.title = tag, tag
        self._boxes, self._reason, self._boom = boxes, reason, boom

    def available(self):
        return self._reason

    def find(self, case, frames):
        if self._boom:
            raise self._boom
        return list(self._boxes or [])


class Frames:
    path = __import__("pathlib").Path("видео.MP4")

    def frame(self, idx):
        return np.zeros((1080, 1920, 3), dtype=np.uint8)


# ---- Три исхода -------------------------------------------------------------


def test_working_method_is_scored():
    result = run_case(case(), Fake("точный", boxes=DOORS), Frames())
    assert result.status == "ок"
    assert result.score is not None and result.score.found == 2


def test_missing_method_is_reported_not_zeroed():
    """«Не установлен» — не ноль найденных: ноль был бы неправдой о методе."""
    result = run_case(case(), Fake("нет", reason="нет весов: models/x.pt"), Frames())
    assert result.status == "не установлен"
    assert result.score is None
    assert "models/x.pt" in result.note


def test_crashing_method_does_not_kill_the_run():
    result = run_case(case(), Fake("падучий", boom=RuntimeError("не открывается видео")),
                       Frames())
    assert result.status == "ошибка"
    assert result.score is None
    assert "не открывается видео" in result.note


def test_a_method_that_finds_nothing_is_not_an_error():
    """Пустой ответ — законный результат метода, а не сбой."""
    result = run_case(case(), Fake("пустой", boxes=[]), Frames())
    assert result.status == "ок"
    assert result.score.found == 0 and result.score.total == 2


def test_elapsed_time_is_measured():
    result = run_case(case(), Fake("точный", boxes=DOORS), Frames())
    assert result.elapsed_s >= 0.0


# ---- Реестр -----------------------------------------------------------------


def test_registry_holds_factories_not_instances():
    """Реестр импортируется без torch, transformers и detectron2."""
    from paxcount.bench.methods import REGISTRY

    assert REGISTRY
    assert all(callable(f) for f in REGISTRY.values())


def test_every_method_has_a_unique_tag_and_a_human_title():
    from paxcount.bench.methods import DESCRIPTIONS, REGISTRY

    assert set(DESCRIPTIONS) == set(REGISTRY)
    assert len(set(DESCRIPTIONS.values())) == len(DESCRIPTIONS)
    assert all(d.strip() for d in DESCRIPTIONS.values())


def test_deferred_methods_are_absent_with_a_reason():
    """Отложенные подходы в реестр не кладутся — отложены, а не сломаны."""
    from paxcount.bench.methods import DEFERRED, REGISTRY

    assert not (set(DEFERRED) & set(REGISTRY))
    assert all(reason.strip() for reason in DEFERRED.values())


@pytest.mark.parametrize("tag", [
    "openvocab", "pixels", "ensemble", "edges", "motion",
    "owlv2", "groundingdino", "wheels", "vlpart", "vlm",
])
def test_planned_method_is_registered(tag):
    from paxcount.bench.methods import REGISTRY

    assert tag in REGISTRY
