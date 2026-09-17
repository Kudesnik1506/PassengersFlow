"""Ансамбль: согласие методов вместо их уверенности, число дверей — арбитр.

Объединить кандидатов «по score» нельзя: шкалы уверенности у методов разные и
несопоставимые — у YOLOWorld это вероятность класса, у пиксельного метода
адаптивный процентиль, у краёв вообще нормированная энергия градиента. Отбор по
такому числу выберет не лучший кандидат, а самый громкий метод.

Сопоставимая величина ровно одна: сколько РАЗНЫХ методов назвали это место.
Она и работает весом. Дальше вступает арбитр из решения 038 — из согласованных
мест берётся столько, сколько у машины дверей по таблице 3, а недостача
объявляется дверью за кадром только при обрезанном крае.
"""

from __future__ import annotations

import numpy as np

from ...doorprop import AGGREGATE_TOLERANCE, DoorCandidate
from ..cases import Case
from ..finders import Box, FrameSource, to_fractions, to_pixels

# Методы, чьё согласие спрашивается по умолчанию. Только местные и дешёвые:
# ансамбль должен оставаться работоспособным без сети и без скачанных весов.
DEFAULT_BASES = ("openvocab", "pixels", "edges")


def consensus_candidates(by_method: dict[str, list[Box]], body: Box) -> list[DoorCandidate]:
    """Кандидаты, взвешенные числом согласных методов.

    Кластеризация по центру, тем же допуском, что и агрегация по кадрам в
    `doorprop`: там он отделяет одну дверь, дрогнувшую между кадрами, от двух
    разных — здесь ровно та же задача, только дрожание идёт от метода к методу.
    """
    marked: list[tuple[float, str, Box]] = []
    for method, boxes in by_method.items():
        for box in boxes:
            frac = to_fractions(box, body)
            marked.append(((frac[0] + frac[2]) / 2, method, frac))
    if not marked:
        return []

    marked.sort(key=lambda t: t[0])
    clusters: list[list[tuple[float, str, Box]]] = [[marked[0]]]
    for item in marked[1:]:
        if item[0] - clusters[-1][-1][0] <= AGGREGATE_TOLERANCE:
            clusters[-1].append(item)
        else:
            clusters.append([item])

    result: list[DoorCandidate] = []
    for cluster in clusters:
        fracs = [f for _, _, f in cluster]
        result.append(DoorCandidate(
            x0=float(np.median([f[0] for f in fracs])),
            y0=float(np.median([f[1] for f in fracs])),
            x1=float(np.median([f[2] for f in fracs])),
            y1=float(np.median([f[3] for f in fracs])),
            # Вес — число РАЗНЫХ методов. Один метод, назвавший место дважды,
            # веса себе не добавляет: иначе выиграл бы самый шумный.
            score=float(len({m for _, m, _ in cluster})),
            method="ensemble",
        ))
    return result


class EnsembleFinder:
    """Согласие базовых методов плюс арбитр по числу дверей."""

    tag = "ensemble"
    title = "ансамбль: согласие + число дверей"

    def __init__(self, bases: tuple[str, ...] = DEFAULT_BASES) -> None:
        self.bases = bases
        self.note = ""

    def _live(self) -> list:
        from . import REGISTRY

        live = []
        for tag in self.bases:
            factory = REGISTRY.get(tag)
            if factory is None:
                continue
            finder = factory()
            if finder.available() is None:
                live.append(finder)
        return live

    def available(self) -> str | None:
        live = self._live()
        if len(live) < 2:
            names = ", ".join(self.bases)
            return (f"согласие нечему выражать: из базовых методов ({names}) "
                    f"доступно {len(live)}, нужно хотя бы два")
        return None

    def find(self, case: Case, frames: FrameSource) -> list[Box]:
        from ...doorprop.layout import layout_from_candidates

        by_method: dict[str, list[Box]] = {}
        for finder in self._live():
            try:
                by_method[finder.tag] = list(finder.find(case, frames))
            except Exception as exc:  # один упавший метод не отменяет согласия
                by_method[finder.tag] = []
                self.note += f"{finder.tag} упал ({type(exc).__name__}); "
        self.note += "участвовали: " + ", ".join(sorted(by_method))

        candidates = consensus_candidates(by_method, case.body_px)
        layout, problems = layout_from_candidates(
            candidates, case.size, case.visit_key, case.camera, case.orientation,
            case.body_px, case.frame_size, case.video, case.frame_idx,
        )
        if problems:
            self.note += " | " + "; ".join(problems)
        if layout is None:
            return []
        return [d.opening_px for d in layout.doors
                if d.in_frame and d.opening_px is not None]
