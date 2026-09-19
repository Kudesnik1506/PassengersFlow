"""Код таблицы 2 по числу дверей, попавших в кадр.

Предшественник — `reconcile.clipped_code` — ставил код по одному признаку:
кузов упёрся в край кадра, значит крайняя дверь за кадром. Замер по 239
обрезанным стоянкам показал цену допущения: у 155 из них за кадром остаётся
меньше 5 % кузова, то есть все двери на месте. Полтораста ложных кодов ушло в
книгу заказчика, и отличить их там от верных нельзя.

Здесь спрашивается то же, что спрашивает таблица 2: сколько у машины дверей
всего и сколько попало в кадр целиком. Разницу называет модель по одному кадру
(`tools/edgedoors.py`), а какой конец кузова за краем — геометрия, потому что
направление движения задано заказчиком на камеру и у модели не спрашивается
вовсе: в пилоте она назвала обрезанный конец «передним» на трёх кадрах подряд,
включая два, где обрез был с противоположных сторон.

Три запрета держат модуль:

* **не угадывать по одному прогону** — решение 024: одиночный ответ модели
  нечем проверить, нужно строгое большинство независимых;
* **не ставить код при разногласии** — пустая графа честнее правдоподобной:
  ложный код в книге неотличим от верного, а заказчик сверяет книгу построчно;
* **не верить числу дверей на слово** — оно сверяется с размером ТС по
  таблице 3, и расхождение уходит заказчику отдельным файлом (18.09).

Толкование самой таблицы 2 сюда не переехало: оно одно на два входа — разметку
и счёт дверей — и живёт в `reconcile.code_for_hidden`.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from typing import Sequence

from ..doorprop.layout import NOSE_LEFT, NOSE_RIGHT
from .model import DOORS_BY_SIZE, VehicleSize
from .reconcile import EDGE_TOLERANCE_PX, code_for_hidden

# Какой конец кузова остался за краем кадра. Двери нумеруются от носа, поэтому
# конец решает, какие именно номера пропали, а не сколько их.
NOSE_CUT = "нос"
TAIL_CUT = "корма"

# Меньше двух прогонов согласием не считается (решение 024).
MIN_RUNS = 2


@dataclass(frozen=True)
class DoorAnswer:
    """Ответ одного прогона: сколько дверей у машины и сколько видно целиком.

    Оба числа от модели, и оба проверяемы: первое — размером ТС по таблице 3,
    второе — согласием прогонов между собой. Направления здесь нет намеренно.
    """

    total: int
    in_frame: int


def cut_end(body_px: tuple[float, float, float, float],
             frame_size: tuple[int, int],
             orientation: str | None) -> tuple[str | None, str | None]:
    """Какой конец кузова ушёл за край кадра. Пара «конец, причина отказа».

    Единственное, что здесь берётся из геометрии, и единственное, что она
    знает наверняка: рамка либо упёрлась в край, либо нет. Сколько за этим
    краем осталось кузова и попала ли туда дверь — не её вопрос.
    """
    if orientation not in (NOSE_LEFT, NOSE_RIGHT):
        return None, ("направление движения для камеры не задано — какой конец "
                       "срезан, неизвестно, а зеркальный код хуже пустой графы")

    left, _, right, _ = body_px
    width = frame_size[0]
    cut_left = left <= EDGE_TOLERANCE_PX
    cut_right = right >= width - EDGE_TOLERANCE_PX
    if not cut_left and not cut_right:
        return None, None
    if cut_left and cut_right:
        return None, ("кузов срезан с обеих сторон: таблица 2 описывает начало "
                       "и конец по отдельности, такого случая в ней нет")

    nose_cut = cut_right if orientation == NOSE_RIGHT else cut_left
    return (NOSE_CUT if nose_cut else TAIL_CUT), None


def hidden_doors(total: int, in_frame: int, end: str) -> list[int]:
    """Номера дверей, не попавших в кадр, — от носа, как нумерует таблица 2.

    Пропавшие двери идут подряд от срезанного конца: кадр отрезает кузов одной
    прямой, а не выбирает двери по одной.
    """
    missing = total - in_frame
    if missing <= 0:
        return []
    if end == NOSE_CUT:
        return list(range(1, missing + 1))
    return list(range(total - missing + 1, total + 1))


def code_from_answer(answer: DoorAnswer,
                      end: str) -> tuple[int | None, str | None]:
    """Код таблицы 2 по согласованному ответу. Пара «код, пояснение».

    `None` без пояснения — кода не нужно: все двери в кадре. Именно этот
    случай геометрия и не умела назвать.
    """
    if answer.total <= 0:
        return None, "дверей у машины не названо — кода нет"
    if answer.in_frame > answer.total:
        return None, (f"в кадре дверей {answer.in_frame}, а у машины всего "
                       f"{answer.total} — ответ несвязный, кода нет")
    return code_for_hidden(hidden_doors(answer.total, answer.in_frame, end),
                            answer.total)


def agreed(answers: Sequence[DoorAnswer]) -> tuple[DoorAnswer | None, str]:
    """Ответ, который назвало строгое большинство прогонов, или отказ.

    Большинство именно строгое — больше половины, а не «чаще прочих». Двое
    против двоих это спор, и разрешать его порядком в `Counter` значит бросать
    жребий и выдавать результат за согласие.

    Среднее не берётся никогда: полторы двери в кадре не бывает.
    """
    if not answers:
        return None, "прогонов нет"
    if len(answers) < MIN_RUNS:
        return None, ("один прогон — это мнение, а не согласие: нужно минимум "
                       "двух независимых (решение 024)")

    total, total_note = _majority([a.total for a in answers], "дверей всего")
    in_frame, frame_note = _majority([a.in_frame for a in answers],
                                      "попало в кадр")
    notes = [n for n in (total_note, frame_note) if n]
    if notes:
        return None, "; ".join(notes)
    return DoorAnswer(total=total, in_frame=in_frame), ""


def _majority(values: list[int], label: str) -> tuple[int | None, str]:
    best, hits = Counter(values).most_common(1)[0]
    if hits * 2 > len(values):
        return best, ""
    return None, f"по «{label}» прогоны разошлись: {sorted(values)}"


def size_mismatch(total_seen: int, size: VehicleSize | None) -> str | None:
    """Расхождение числа дверей с размером ТС — текст для отдельного файла.

    Заказчик (18.09): счёт дверей ведётся ради этой сверки, и расхождение
    подсвечивается записью с пояснением. В книгу оно не идёт: кто именно
    ошибся — модель в счёте или оператор в размере — отсюда не видно, а
    молча исправить чужую графу по догадке нельзя.

    У рельсового транспорта размер — это вагоны, а не двери (таблица 3 его не
    описывает), и сверять там нечего.
    """
    expected = DOORS_BY_SIZE.get(size) if size is not None else None
    if expected is None or total_seen == expected:
        return None
    return (f"дверей насчитано {total_seen}, а размер «{size.value}» "
             f"по таблице 3 означает {expected}")
