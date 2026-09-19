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
  повод подменить одно другим; а отказ портала объясняется в самой книге, в
  графе комментария. Заказчик видит в графе номера бортовой вместо
  государственного и вправе знать почему: на 295 строках таких девять
  (решение 076, дополнено 18.09).

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


def _unknown_plate_note(board: str | None, moment: datetime) -> str:
    """Объяснение для графы комментария: почему в номере стоит бортовой.

    Словами и с датой: портал отвечает на КОНКРЕТНЫЙ день (`PortalClient.lookup`),
    и «нет в базе» без даты читалось бы как «машины не существует».

    Кодом это быть не может: графа M проверяется по таблице 2, а таблица 2 — про
    двери в кадре. Текст уживается с кодом в одной клетке через `comment_cell`.
    """
    return (f"госномер не найден: борт {board} порталу неизвестен "
             f"на {moment:%d.%m.%Y}")


def with_plate(row: DeliveryRow, moment: datetime,
                ask: Ask) -> tuple[DeliveryRow, frozenset[str]]:
    """Строка с государственным номером из портала и то, что мы в ней изменили.

    Вторым значением — поля, которые книга обязана пометить: подстановка
    госномера это правка чужой графы, а всё наше в книге видно цветом
    (решение 070). Не нашли номера — не меняли и графу: в ней остался
    бортовой, записанный оператором, и красить нечего.

    `moment` выбирает смену: одна машина за день ходит по разным маршрутам, и
    без времени ответ верен наполовину (`portal.PortalClient.lookup`).
    """
    if row.kind not in KINDS_NEEDING_STATE_NUMBER:
        return row, frozenset()   # у рельсового госномера нет, спрашивать нечего
    if row.state_number or not row.board_number:
        return row, frozenset()   # знаем сами или спрашивать не о чем

    answer = ask(row.board_number, moment.strftime("%Y-%m-%d"), row.kind, moment)
    overrides = dict(row.overrides)
    disputed = dict(row.disagreements)

    if isinstance(answer, NotFound):
        overrides["госномер"] = f"портал: {answer.reason}"
        return row.model_copy(update={
            "overrides": overrides,
            "notes": row.notes + (_unknown_plate_note(row.board_number, moment),),
        }), frozenset()

    assert isinstance(answer, VehicleInfo)
    update: dict = {}
    changed: set[str] = set()
    if answer.state_number:
        update["state_number"] = answer.state_number
        changed.add("number")
        overrides["госномер"] = (
            f"портал по борту {row.board_number}"
            + (f", смена выбрана: {answer.selected_by}" if answer.shifts_total > 1 else "")
        )
    else:
        overrides["госномер"] = f"портал знает борт {row.board_number}, но номера не дал"
        update["notes"] = row.notes + (_unknown_plate_note(row.board_number, moment),)

    # Маршрут портала, сказанный иначе, — это спор, и он идёт в книгу: третье
    # мнение не становится согласованным оттого, что оно третье.
    theirs = (answer.route or "").strip()
    ours = (row.route or "").strip()
    if theirs and ours and theirs != ours:
        disputed["маршрут"] = f"портал говорит {theirs}, в строке {ours}"
    elif theirs and not ours:
        disputed["маршрут"] = f"портал говорит {theirs}, у нас маршрут не опознан"

    return row.model_copy(update={**update, "overrides": overrides,
                                   "disagreements": disputed}), frozenset(changed)
