"""Сверка нашей строки с записью оператора: что совпало, что разошлось.

Выгрузка оператора — черновик (решение 025). Он мог машину пропустить, нажать
дважды, ошибиться при вводе. Поэтому она здесь не источник истины и не мусор, а
второе независимое свидетельство: где мы с ним сходимся, строка надёжна, где
расходимся — заказчик обязан это видеть, а не получить наше слово молча.

Сверка идёт по ЛИЧНОСТИ машины — бортовому номеру. Не по времени: на боевой
смене часы оператора и часы камеры разошлись на 6:52–7:18 (борта 1596, 38157,
7861), и сопоставление по времени связало бы чужие пары. Само это расхождение —
находка, и оно попадает в `mismatched` как поле `time`.

Три исхода, и они различимы намеренно:

* **совпало** — запись есть, поле сходится;
* **разошлось** — запись есть, поле не сходится (`mismatched`);
* **оператор молчит** — записи нет вовсе (`missing`). Это не совпадение и не
  расхождение: подтверждения просто нет, и выдавать его за согласие нельзя.

Наполненность берётся у оператора целиком: своей моделью мы её не считаем
(решение 032), и другого источника у нас нет.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .model import DeliveryRow
from .operator import OperatorRecord

# Насколько времена могут разойтись, оставаясь «тем же моментом». Бланк хранит
# часы и минуты, поэтому секунды здесь не значат ничего, а минута — значит.
TIME_TOLERANCE = timedelta(seconds=60)
# Насколько далеко имеет смысл искать запись про ту же машину. Одна машина
# проходит остановку за смену не раз, и ближайшая по времени запись без границы
# находится ВСЕГДА: наполненность и маршрут приедут от другого рейса, а
# расхождение часов будет измерено по чужой паре. Граница шире расхождения часов
# (на боевой смене 6:52–7:18) и уже интервала между заездами (часы).
MATCH_WINDOW = timedelta(minutes=30)

FIELDS = ("kind", "route", "size", "board", "time")


@dataclass(frozen=True)
class Agreement:
    """Итог сверки одной строки. `record is None` — оператор молчит."""

    record: OperatorRecord | None
    mismatched: frozenset[str]
    occupancy: str | None = None

    @property
    def missing(self) -> bool:
        return self.record is None


def _same_day_moment(row: DeliveryRow, record: OperatorRecord):
    """Момент нашей строки на календаре записи оператора."""
    return record.created.replace(hour=row.hours, minute=row.minutes, second=0)


def _find(row: DeliveryRow, records: list[OperatorRecord]) -> OperatorRecord | None:
    board = (row.board_number or "").strip()
    if not board:
        return None
    same = [r for r in records if r.board.strip() == board and r.stop == row.stop]
    if not same:
        return None
    nearest = min(same, key=lambda r: abs(r.created - _same_day_moment(row, r)))
    if abs(nearest.created - _same_day_moment(row, nearest)) > MATCH_WINDOW:
        return None
    return nearest


def agree(row: DeliveryRow, records: list[OperatorRecord]) -> Agreement:
    """Сверяет строку с выгрузкой. Ничего не правит — только называет."""
    record = _find(row, records)
    if record is None:
        return Agreement(None, frozenset())

    bad: set[str] = set()
    if row.kind.value.strip() != record.kind.strip():
        bad.add("kind")
    if (row.route or "").strip() != record.route.strip():
        bad.add("route")
    size = row.size.value.strip() if row.size else ""
    if size != record.size.strip():
        bad.add("size")
    if (row.board_number or "").strip() != record.board.strip():
        bad.add("board")
    if abs(record.created - _same_day_moment(row, record)) > TIME_TOLERANCE:
        bad.add("time")
    return Agreement(record, frozenset(bad), occupancy=record.occupancy)
