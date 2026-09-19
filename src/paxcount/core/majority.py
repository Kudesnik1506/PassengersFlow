"""Строгое большинство прогонов — одно правило на весь проект.

Модель зовётся несколько раз, и ответы надо свести в один или честно отказаться.
Правило простое и одно: значение берётся, если его назвало БОЛЬШЕ ПОЛОВИНЫ
прогонов. Не «чаще прочих»: двое против двоих — это спор, а разрешать спор
порядком в `Counter` значит бросать жребий и выдавать жребий за согласие.

Среднее не берётся никогда: полторы двери в кадре не бывает, а половина
бортового номера — не номер.

Сколько прогонов нужно для самой попытки — вопрос зовущего (решение 024
требует двух независимых). Здесь только «сошлись или нет».
"""

from __future__ import annotations

from collections import Counter
from typing import Hashable, Sequence, TypeVar

T = TypeVar("T", bound=Hashable)


def strict(values: Sequence[T], label: str) -> tuple[T | None, str]:
    """Значение строгого большинства и причина отказа, если его нет."""
    if not values:
        return None, f"по «{label}» прогонов нет"
    best, hits = Counter(values).most_common(1)[0]
    if hits * 2 > len(values):
        return best, ""
    shown = ", ".join(str(v) for v in sorted(values, key=str))
    return None, f"по «{label}» прогоны разошлись: {shown}"
