"""Замена бортового номера государственным через портал.

Инструкция заказчика: автобусу в графу номера идёт государственный, троллейбусу
и трамваю — бортовой. Оператор записывает бортовой всем, значит замена — наша
работа, и дорога у неё одна: `борт → портал → госномер`. Обратного пути нет,
поиск по государственному номеру портал не поддерживает (решение 026).

Правила здесь про то, чего делать нельзя:

* **не выдумывать** — портал не знает машину, в графе остаётся бортовой. В
  принятом заказчиком файле такие строки есть: «34547 -нет в базе»;
* **не затирать** — бортовой из строки не исчезает: он единственный ключ, по
  которому спор о машине потом разбирается;
* **не молчать** — маршрут портала, не совпавший с нашим, это расхождение, а не
  повод подменить одно другим.

Сеть сюда не входит: `ask` передаётся снаружи. Так тесты идут без обращения к
государственному порталу, а ответы с реальными номерами не попадают в
репозиторий.
"""

from __future__ import annotations

from datetime import datetime
from typing import Callable

from ..portal import Answer, NotFound, VehicleInfo
from .model import KINDS_NEEDING_STATE_NUMBER, DeliveryRow

Ask = Callable[[str, str, object, datetime], Answer]


def with_plate(row: DeliveryRow, moment: datetime, ask: Ask) -> DeliveryRow:
    """Строка с государственным номером из портала, если его удалось узнать.

    `moment` выбирает смену: одна машина за день ходит по разным маршрутам, и
    без времени ответ верен наполовину (`portal.PortalClient.lookup`).
    """
    if row.kind not in KINDS_NEEDING_STATE_NUMBER:
        return row          # у рельсового госномера нет, спрашивать нечего
    if row.state_number or not row.board_number:
        return row          # знаем сами или спрашивать не о чем

    answer = ask(row.board_number, moment.strftime("%Y-%m-%d"), row.kind, moment)
    overrides = dict(row.overrides)

    if isinstance(answer, NotFound):
        overrides["госномер"] = f"портал: {answer.reason}"
        return row.model_copy(update={"overrides": overrides})

    assert isinstance(answer, VehicleInfo)
    update: dict = {}
    if answer.state_number:
        update["state_number"] = answer.state_number
        overrides["госномер"] = (
            f"портал по борту {row.board_number}"
            + (f", смена выбрана: {answer.selected_by}" if answer.shifts_total > 1 else "")
        )
    else:
        overrides["госномер"] = f"портал знает борт {row.board_number}, но номера не дал"

    theirs = (answer.route or "").strip()
    ours = (row.route or "").strip()
    if theirs and ours and theirs != ours:
        overrides["маршрут"] = f"портал говорит {theirs}, в строке {ours}"
    elif theirs and not ours:
        overrides["маршрут"] = f"портал говорит {theirs}, у нас маршрут не опознан"

    return row.model_copy(update={**update, "overrides": overrides})
