"""Разбор ответа счётной модели и согласие независимых прогонов.

Два правила, каждое куплено ошибкой.

**Непонятный ответ — исключение, а не ноль.** Ответ, из которого не вынулось
число, и честное «событий не было» попадают в таблицу одинаковым нулём, но
означают разное: «мы не знаем» против «мы посмотрели, там пусто». Слитые
вместе, они дают нули, которых никто не наблюдал, и прячут поломку клиента за
правдоподобной строкой.

**Расхождение не усредняется.** Если один прогон увидел 3 входа, а другой 5,
то 4 — это число, которого не видел никто. Разошлись — решает третий прогон
большинством (решение 024), а если и он не помог, строка уходит человеку.
Ровно так же устроена ручная методика: диапазоны запрещены, среднее между
двумя наблюдениями — не наблюдение.

Направления судятся по отдельности: прогоны могут сойтись по входу и разойтись
по выходу, и терять согласие по входу из-за этого незачем.
"""

from __future__ import annotations

import json
import re
from collections import Counter
from dataclasses import dataclass, field

DIRECTIONS = frozenset({"in", "out"})

# Ответ бывает обёрнут в ```json ... ``` или окружён пояснениями — вынимаем
# первый объект верхнего уровня, а не требуем чистоты от собеседника.
_OBJECT_RE = re.compile(r"\{.*\}", re.DOTALL)


class AnswerFormatError(ValueError):
    """Из ответа не вынулось число. Это «неизвестно», и это не ноль."""


@dataclass(frozen=True)
class CountEvent:
    t: float
    direction: str


@dataclass(frozen=True)
class CountAnswer:
    """Один разобранный ответ модели. `raw` хранится для разбора спора."""

    boarded: int
    alighted: int
    events: tuple[CountEvent, ...] = ()
    doubts: str = ""
    raw: str = ""


@dataclass(frozen=True)
class Consensus:
    """Итог сверки прогонов. `None` в числе — согласия нет, нужен человек."""

    boarded: int | None
    alighted: int | None
    agreed: bool
    reason: str
    answers: tuple[CountAnswer, ...] = field(default=())


def parse_answer(text: str) -> CountAnswer:
    """Вынимает JSON из ответа модели. Всё, что не разобралось, — ошибка."""
    match = _OBJECT_RE.search(text or "")
    if match is None:
        raise AnswerFormatError(
            f"в ответе нет объекта JSON: {(text or '')[:120]!r}"
        )
    try:
        payload = json.loads(match.group(0))
    except json.JSONDecodeError as exc:
        raise AnswerFormatError(f"ответ не разбирается как JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise AnswerFormatError("в ответе не объект, а что-то другое")

    boarded = _count(payload, "boarded")
    alighted = _count(payload, "alighted")
    events = tuple(_event(e) for e in payload.get("events") or [])
    doubts = str(payload.get("doubts") or "")
    return CountAnswer(boarded=boarded, alighted=alighted, events=events,
                        doubts=doubts, raw=text)


def _count(payload: dict, key: str) -> int:
    if key not in payload:
        raise AnswerFormatError(f"в ответе нет обязательного поля {key!r}")
    value = payload[key]
    if isinstance(value, bool) or not isinstance(value, int):
        raise AnswerFormatError(f"поле {key!r} не целое число: {value!r}")
    if value < 0:
        raise AnswerFormatError(f"поле {key!r} отрицательное: {value}")
    return value


def _event(raw: object) -> CountEvent:
    if not isinstance(raw, dict):
        raise AnswerFormatError(f"событие не объект: {raw!r}")
    direction = str(raw.get("direction", ""))
    if direction not in DIRECTIONS:
        raise AnswerFormatError(
            f"направление {direction!r} вне {sorted(DIRECTIONS)}"
        )
    try:
        t = float(raw.get("t"))
    except (TypeError, ValueError) as exc:
        raise AnswerFormatError(f"время события не число: {raw.get('t')!r}") from exc
    return CountEvent(t=t, direction=direction)


def consensus(answers: list[CountAnswer]) -> Consensus:
    """Сводит прогоны в одно число или честно отказывается.

    Один прогон согласием не считается: решение 024 требует двух независимых,
    потому что одиночный ответ модели нечем проверить.
    """
    if not answers:
        return Consensus(None, None, False, "прогонов нет", ())
    if len(answers) == 1:
        return Consensus(
            None, None, False,
            "один прогон — это мнение, а не согласие: нужно минимум двух "
            "независимых (решение 024)",
            tuple(answers),
        )

    boarded, boarded_note = _majority([a.boarded for a in answers], "вошло")
    alighted, alighted_note = _majority([a.alighted for a in answers], "вышло")
    notes = [n for n in (boarded_note, alighted_note) if n]
    agreed = boarded is not None and alighted is not None
    reason = "прогоны сошлись" if agreed else "; ".join(notes) + " — нужен человек"
    return Consensus(boarded, alighted, agreed, reason, tuple(answers))


def _majority(values: list[int], label: str) -> tuple[int | None, str]:
    """Значение, которое назвало большинство. Среднее не берётся никогда."""
    counts = Counter(values)
    best, hits = counts.most_common(1)[0]
    if hits >= 2:
        return best, ""
    return None, f"по «{label}» прогоны разошлись: {sorted(values)}"
