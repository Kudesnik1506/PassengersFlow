"""Эталонный счёт по дверям — `data/truth/door_counts.csv`.

Это та самая мера, ради которой всё остальное и строится: числа здесь считал
человек по кадрам, а не алгоритм. Формирует их владелец, вслепую от выгрузки
оператора и от наших прогонов — готовый ответ под рукой подгоняет счёт к нему,
а не проверяет его.

Неизмеренное пишется словом `N/A`, как и в бланке заказчика. Разница с нулём не
формальная: ноль — это измерение («через эту дверь никто не прошёл»), `N/A` —
его отсутствие, и свести их вместе значит выдать несчитанное за совпадение с
любым результатом. Пустая клетка неотличима от забытой и потому не пишется:
`N/A` — заявление, которое видно, пустота — молчание, которое читается как
угодно. Файлы, заполненные до этой конвенции, читаются по-прежнему.

Пустыми (то есть `N/A`) остаются ровно двери ВНЕ КАДРА: через них считать было
нечего, и именно из этого потом растёт код 1-8 в бланке. У двери в кадре `N/A`
не бывает — там ноль, даже если никто не прошёл.

Итог визита здесь не хранится. Он складывается из дверей (`totals`), потому что
отдельная графа «вошло всего» — копия, которая разъезжается с разбивкой ровно
тогда, когда в разбивку внесут поправку.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from pathlib import Path

FIELDS = ("visit", "door", "boarded", "alighted")
NA = "N/A"


@dataclass(frozen=True)
class DoorCount:
    """Сколько человек прошло через одну дверь одного визита."""

    visit: str
    door: int
    boarded: int | None
    alighted: int | None


def _int(value: str | None) -> int | None:
    value = (value or "").strip()
    if not value or value.upper() == NA:
        return None
    return int(value)


def totals(rows: list[DoorCount]) -> dict[str, tuple[int | None, int | None]]:
    """Итоги визитов: сумма по дверям. `None` — по этому визиту не считали."""
    out: dict[str, tuple[int | None, int | None]] = {}
    for row in rows:
        inn, alt = out.get(row.visit, (None, None))
        if row.boarded is not None:
            inn = (inn or 0) + row.boarded
        if row.alighted is not None:
            alt = (alt or 0) + row.alighted
        out[row.visit] = (inn, alt)
    return out


def load(path: Path) -> list[DoorCount]:
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        return [
            DoorCount(visit=(row.get("visit") or "").strip(),
                       door=int(row["door"]),
                       boarded=_int(row.get("boarded")),
                       alighted=_int(row.get("alighted")))
            for row in csv.DictReader(f)
        ]


def save(path: Path, rows: list[DoorCount]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "visit": row.visit, "door": row.door,
                "boarded": NA if row.boarded is None else row.boarded,
                "alighted": NA if row.alighted is None else row.alighted,
            })
