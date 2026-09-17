"""Прогон одного метода по одному случаю: три исхода, ни одного молчаливого.

Метод либо отработал (и получил оценку), либо не установлен, либо упал. Свести
последние два к нулю найденных дверей нельзя: ноль означает «метод посмотрел и
не нашёл», и на выборке в пять машин эта разница и есть весь вывод.
"""

from __future__ import annotations

import time
from dataclasses import dataclass

from .cases import Case
from .finders import DoorFinder, FrameSource
from .score import CaseScore, score_case

OK = "ок"
MISSING = "не установлен"
FAILED = "ошибка"


@dataclass(frozen=True)
class MethodResult:
    """Что метод сделал на одном визите."""

    case: Case
    method: str
    title: str
    status: str
    score: CaseScore | None = None
    predicted: tuple = ()
    # Уверенности метода в том же порядке, что рамки. Пустые — метод их не
    # сообщает; тогда отбор «первые K» произволен, и это видно, а не скрыто.
    scores: tuple = ()
    note: str = ""
    elapsed_s: float = 0.0


def run_case(case: Case, finder: DoorFinder, frames: FrameSource) -> MethodResult:
    """Один метод на одном визите. Исключение метода не прекращает прогон."""
    reason = finder.available()
    if reason:
        return MethodResult(case=case, method=finder.tag, title=finder.title,
                             status=MISSING, note=reason)

    started = time.perf_counter()
    try:
        predicted = list(finder.find(case, frames))
    except Exception as exc:  # метод чужой — падать он может как угодно
        return MethodResult(
            case=case, method=finder.tag, title=finder.title, status=FAILED,
            note=f"{type(exc).__name__}: {exc}",
            elapsed_s=time.perf_counter() - started,
        )
    elapsed = time.perf_counter() - started
    return MethodResult(
        case=case, method=finder.tag, title=finder.title, status=OK,
        score=score_case(list(case.doors_visible), predicted),
        predicted=tuple(predicted),
        scores=tuple(float(s) for s in getattr(finder, "scores", ()) or ()),
        elapsed_s=elapsed,
        # Метод может сообщить, в каком режиме отработал (по образцу или по
        # тексту, на каком устройстве). Без этого две строки таблицы с разными
        # числами выглядели бы как один и тот же опыт.
        note=str(getattr(finder, "note", "") or ""),
    )
