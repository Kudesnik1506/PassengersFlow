"""Перепроверка граф оператора: назвать расхождение, ничего не исправив.

Оператор заполняет вид и размер ТС на глаз, стоя на остановке, по машине,
которая уже отъезжает. Проверить его есть чем, и оба способа независимы от
него самого:

* **таблица 3** перечисляет существующие пары «вид + размер» и прямо называет
  невозможные — «Таких нет», «Таких нет в СПб» (`table3`);
* **двери в кадре** дают размер, не спрашивая ни оператора, ни разметку
  (`edgedoors`), а размер сужает вид.

Ни одна проверка графу не правит. Кто ошибся — оператор в виде, оператор в
размере или наша модель в счёте дверей, — из строки не видно, и молча подменить
чужую запись догадкой значит выдать третье мнение за согласованное. Всё
найденное уходит в графу S (решение 087), разбирает человек.

Проверка молчит там, где сравнивать не с чем: пустая графа размера — не
расхождение, а незаполненная графа, и дверей не считали — не повод спорить о
размере.
"""

from __future__ import annotations

from . import table3
from .edgedoors import size_mismatch
from .model import DeliveryRow
from .reconcile import size_from_doors

# Ключи графы S. Названы графами бланка, а не полями модели: читает их человек,
# который держит перед глазами книгу, а не наш код.
PAIR = "вид и размер"
SIZE = "размер"
KIND = "вид ТС"


def with_checks(row: DeliveryRow, doors_seen: int | None = None) -> DeliveryRow:
    """Строка с записанными расхождениями. Сами графы остаются как были.

    `doors_seen` — сколько дверей у машины насчитано по кадру. `None` означает,
    что не считали: тогда судится только пара «вид + размер», которая видна и
    без кадра.
    """
    found = dict(row.disagreements)

    pair = table3.pair_note(row.kind, row.size)
    if pair:
        found[PAIR] = pair

    by_doors = size_mismatch(doors_seen, row.size) if doors_seen is not None else None
    if by_doors:
        found[SIZE] = by_doors

    kind = _kind_note(row, doors_seen)
    if kind:
        found[KIND] = kind

    if found == row.disagreements:
        return row
    return row.model_copy(update={"disagreements": found})


def _kind_note(row: DeliveryRow, doors_seen: int | None) -> str | None:
    """Спорен ли вид ТС по числу дверей, которое мы насчитали сами.

    Это единственная проверка вида, работающая на строке без нашей расшифровки:
    сверка с оператором (`agreement`) требует нашей записи о той же машине, а
    её есть на шесть визитов из трёхсот.

    Пара «вид + размер» разбирается отдельно (`table3.pair_note`): если оператор
    сам написал невозможную пару, ссылаться на двери незачем — расхождение видно
    и без них.
    """
    if doors_seen is None or row.kind is None or row.size is not None:
        return None
    measured = size_from_doors(doors_seen)
    if measured is None:
        return None
    allowed = table3.kinds_for_size(measured)
    if row.kind in allowed:
        return None
    return (f"дверей насчитано {doors_seen} — это «{measured.value}» по таблице 3, "
             f"а такого размера у вида «{row.kind.value}» не бывает")
