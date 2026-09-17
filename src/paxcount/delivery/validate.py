"""Правила заказчика, записанные исполняемым кодом.

Принцип 7: то, что можно проверить синтаксически, проверяется синтаксически.
Правила инструкции — ровно такой случай: «госномера пишем обязательно»,
«не более 5 % транспорта с неопознанным маршрутом», «имя файла
ГГГГ-ММ-ДД-ннннн». Каждое из них заказчик проверит на своей стороне, и файл
вернётся, поэтому проверять их дисциплиной исполнителя нельзя.

Здесь только правила. Числа, формально допустимые, но непохожие на смену, —
в `plausibility`: это подозрение, а не нарушение, и смешивать их значит либо
блокировать поставку из-за догадки, либо пропускать нарушение как догадку.
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .model import KINDS_NEEDING_STATE_NUMBER, DeliveryRow

# «Но количество транспорта с неопознанным номером маршрута не должно превышать
# 5% от всего транспорта на остановке» — инструкция расшифровщика.
UNKNOWN_ROUTE_LIMIT = 0.05


@dataclass(frozen=True)
class Problem:
    """Нарушение правила заказчика. `row` — номер строки бланка, 0 — весь файл."""

    row: int
    field: str
    message: str


def delivery_filename(date: str, group: str, stop: str) -> str:
    """Имя файла поставки.

    Инструкция говорит «ГГГГ-ММ-ДД-ннннн, где ннннн — номер остановки», но
    принятый заказчиком файл назван `2026-05-19-1317-1715-15182.xlsx`, то есть
    между датой и остановкой стоит ещё и группа. Следуем принятому файлу, а не
    букве инструкции: приняли именно его.
    """
    return f"{date}-{group}-{stop}.xlsx"


def validate(rows: list[DeliveryRow]) -> list[Problem]:
    """Проверяет строки по правилам инструкции. Пустой список — можно отдавать."""
    problems: list[Problem] = []

    for i, row in enumerate(rows, start=2):  # 1 — шапка бланка
        if row.kind in KINDS_NEEDING_STATE_NUMBER and not row.state_number:
            problems.append(Problem(
                i, "state_number",
                "нет госномера, а заказчик требует его обязательно для этого вида ТС",
            ))
        if not row.route and not (row.state_number or row.board_number):
            problems.append(Problem(
                i, "route",
                "маршрут не опознан, и в графе не указан номер ТС — "
                "инструкция требует писать номер справа в той же строке",
            ))
        if not row.video:
            problems.append(Problem(i, "video", "не указано название видеофайла"))
        if not row.operator:
            problems.append(Problem(i, "operator", "не указана фамилия расшифровщика"))

    if rows:
        unknown = sum(1 for r in rows if not r.route)
        share = unknown / len(rows)
        if share > UNKNOWN_ROUTE_LIMIT:
            problems.append(Problem(
                0, "route",
                f"маршрут не опознан у {unknown} из {len(rows)} ТС "
                f"({share:.0%}) — заказчик допускает не более "
                f"{UNKNOWN_ROUTE_LIMIT:.0%}",
            ))

    # Одна и та же машина дважды в одну минуту — это почти наверняка один визит,
    # разбитый надвое (склейка визитов или разрез файла), а не два заезда.
    seen = Counter(
        (r.number, r.hours, r.minutes) for r in rows if r.number
    )
    for (number, hours, minutes), count in seen.items():
        if count > 1:
            problems.append(Problem(
                0, "number",
                f"ТС {number} встречается дважды в {hours:02d}:{minutes:02d} "
                f"({count} строки) — повтор визита или разрез файла",
            ))

    return problems
