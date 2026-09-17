"""Сборка итоговой книги: лента машин в том порядке, в каком они пришли.

Бланк заказчика — не сводка, а ЛЕНТА: строка на каждое ТС, строки по времени.
Наши визиты размечены на трёх камерах, и часы камер идут врозь — три
независимые пары К3↔К2 за одно утро дали −419 / −418 / −428 с
(`delivery.clocks`). Сортировать визиты по «своему» времени значит выдать
последовательность, которой на остановке не было, и сделать это молча.

Поэтому порядок строится только на общей шкале (ноль — К2, решение 036), а
время в бланке пишется тоже на ней: заказчик сверяет ленту с выгрузкой
оператора, у которой часы одни.

Визит, для которого поправки нет, из книги не выбрасывается. Правило владельца
(`delivery.reconcile`): строка не пропадает из-за того, что мы чего-то не
знаем, — недостающая строка видна заказчику сразу, а незаполненная графа
предусмотрена инструкцией. Такие визиты уходят в конец ленты с названной
причиной.

Счёт этот модуль не ведёт и не правит: `None` означает «не считали» и остаётся
`None` до самой книги. Ноль — измерение, `None` — его отсутствие.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from .clocks import ClockRecord, to_reference
from .model import DeliveryRow
from .validate import delivery_filename
from .reconcile import VisitFacts, build_row


@dataclass(frozen=True)
class Visit:
    """Визит и файл камеры, на которой он размечен.

    Файл нужен потому, что поправка часов известна на файл, а не на камеру:
    соседний файл той же камеры её не одалживает.
    """

    facts: VisitFacts
    file: str


@dataclass(frozen=True)
class Placed:
    """Визит, поставленный на общую шкалу. `reference_ts is None` — не удалось."""

    visit: Visit
    reference_ts: datetime | None
    problem: str | None = None


def in_route_order(visits: list[Visit], records: list[ClockRecord]) -> list[Placed]:
    """Лента визитов по общей шкале; неразмещённые — в конец, с причиной."""
    placed: list[Placed] = []
    for visit in visits:
        try:
            moment = to_reference(visit.facts.stop_ts, visit.facts.camera,
                                   visit.file, records)
        except LookupError as exc:
            placed.append(Placed(visit, None, str(exc)))
        else:
            placed.append(Placed(visit, moment))
    known = sorted((p for p in placed if p.reference_ts is not None),
                    key=lambda p: p.reference_ts)
    return [*known, *(p for p in placed if p.reference_ts is None)]


def book(
    placed: list[Placed],
    *,
    group: str,
    stop: str,
    operator: str,
    notes_for: dict[str, tuple[str, ...]] | None = None,
) -> list[DeliveryRow]:
    """Строки бланка в порядке ленты.

    Время строки берётся с общей шкалы. У визита без поправки шкалы нет, и
    писать в бланк его «своё» время нельзя: на ленте заказчика оно означало бы
    другой момент. Такая строка получает время своей камеры и примечание, что
    момент не переведён, — молчаливая подстановка здесь хуже пустой графы.
    """
    notes_for = notes_for or {}
    rows: list[DeliveryRow] = []
    for item in placed:
        facts = item.visit.facts
        notes = notes_for.get(facts.visit_key, ())
        if item.problem:
            notes = (*notes, f"момент не переведён на общую шкалу: {item.problem}")
        moment = item.reference_ts or facts.stop_ts
        rows.append(build_row(
            VisitFacts(**{**facts.__dict__, "stop_ts": moment}),
            group=group, stop=stop, video=item.visit.file, operator=operator,
            notes=notes, camera_ts=facts.stop_ts,
        ))
    return rows


def book_filename(rows: list[DeliveryRow], *, group: str, stop: str) -> str:
    """Имя файла поставки по дате самой книги.

    В графе B бланка дата записана как ДД.ММ.ГГГГ, а имя файла требует
    ГГГГ-ММ-ДД. Это разные записи одной даты, и заменой точек на дефисы одна в
    другую не превращается. Ошибка тихая: цифры все на месте, а файл по дате не
    находится, — поэтому дата разбирается, а не переставляется.
    """
    day = datetime.strptime(rows[0].date, "%d.%m.%Y").date()
    if not group:
        # Группа ОП административная и до её получения бывает неизвестна.
        # Пустая часть оставила бы в имени двойной дефис — след отсутствующего
        # поля, который читается как дефект поставки.
        return f"{day.isoformat()}-{stop}.xlsx"
    return delivery_filename(day.isoformat(), group, stop)
