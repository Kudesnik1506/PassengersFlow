"""Эталонные строки визитов — `data/truth/rows.csv`.

Строка эталона повторяет графы бланка заказчика, но живёт по своим правилам.
Главное из них: время приезда хранится по часам КАЖДОЙ камеры отдельно и без
единой поправки. Поправка — величина измеренная и пока не константа (у К3 три
независимые пары дали 418, 419 и 428 с), и записать пересчитанное время как
наблюдение значило бы спрятать этот разброс внутрь эталона, то есть внутрь
меры, которой проверяют всё остальное.

Отсюда же пустая ячейка: она значит «не замерено», а не «ноль» и не «нет».
Пересчёт по поправке делает тот, кто показывает таблицу, и называет его
пересчётом — см. `bench/summary.py`.

Счётные графы (`boarded`/`alighted`) заполняет владелец вслепую от выгрузки
оператора: готовый ответ под рукой подгоняет число к нему, а не проверяет его.
Поэтому модуль их читает и пишет, но ничем не заполняет сам.
"""

from __future__ import annotations

import csv
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from .delivery.model import VehicleSize

FIELDS = (
    "number", "k1_arrival", "k2_arrival", "k3_arrival", "vehicle_kind",
    "route", "board_number", "state_number", "stood_where", "size",
    "doors_total", "boarded", "alighted", "comment",
)


@dataclass(frozen=True)
class TruthRow:
    """Один визит глазами человека. `None` — «не замерено», а не «ноль»."""

    number: str
    k1_arrival: datetime | None
    k2_arrival: datetime | None
    k3_arrival: datetime | None
    vehicle_kind: str | None
    route: str | None
    board_number: str | None
    # Госномер стоит рядом с бортовым, а не вместо него: в бланке заказчика в
    # эту графу у автобусов идёт госномер, но борт читается с кузова раньше и
    # чаще, и терять его при переносе в бланк незачем.
    state_number: str | None
    stood_where: str | None
    size: VehicleSize | None
    doors_total: int | None
    boarded: int | None
    alighted: int | None
    comment: str | None

    def arrival(self, camera: str) -> datetime | None:
        return getattr(self, f"k{camera}_arrival", None)


def _time(value: str | None) -> datetime | None:
    value = (value or "").strip()
    return datetime.fromisoformat(value) if value else None


def _int(value: str | None) -> int | None:
    value = (value or "").strip()
    return int(value) if value else None


def _text(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def load(path: Path) -> list[TruthRow]:
    """Читает эталонные строки. Отсутствующий файл — ещё не начатый эталон."""
    if not path.exists():
        return []
    with path.open(encoding="utf-8", newline="") as f:
        rows = []
        for raw in csv.DictReader(f):
            size = _text(raw.get("size"))
            rows.append(TruthRow(
                number=(raw.get("number") or "").strip(),
                k1_arrival=_time(raw.get("k1_arrival")),
                k2_arrival=_time(raw.get("k2_arrival")),
                k3_arrival=_time(raw.get("k3_arrival")),
                vehicle_kind=_text(raw.get("vehicle_kind")),
                route=_text(raw.get("route")),
                board_number=_text(raw.get("board_number")),
                state_number=_text(raw.get("state_number")),
                stood_where=_text(raw.get("stood_where")),
                size=VehicleSize(size) if size else None,
                doors_total=_int(raw.get("doors_total")),
                boarded=_int(raw.get("boarded")),
                alighted=_int(raw.get("alighted")),
                comment=_text(raw.get("comment")),
            ))
    return rows


def save(path: Path, rows: list[TruthRow]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        for row in rows:
            writer.writerow({
                "number": row.number,
                "k1_arrival": row.k1_arrival.isoformat() if row.k1_arrival else "",
                "k2_arrival": row.k2_arrival.isoformat() if row.k2_arrival else "",
                "k3_arrival": row.k3_arrival.isoformat() if row.k3_arrival else "",
                "vehicle_kind": row.vehicle_kind or "",
                "route": row.route or "",
                "board_number": row.board_number or "",
                "state_number": row.state_number or "",
                "stood_where": row.stood_where or "",
                "size": row.size.value if row.size else "",
                "doors_total": "" if row.doors_total is None else row.doors_total,
                "boarded": "" if row.boarded is None else row.boarded,
                "alighted": "" if row.alighted is None else row.alighted,
                "comment": row.comment or "",
            })
