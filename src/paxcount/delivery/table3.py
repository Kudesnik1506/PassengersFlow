"""Таблица 3 инструкции: какие пары «тип ТС + размер» существуют в Петербурге.

Таблица 3 в инструкции — не справка о марках, а ограничение: она перечисляет,
какие размеры бывают у каждого вида транспорта, и прямо называет невозможные
(«Таких нет», «Таких нет в СПб»). Оператор заполняет обе графы на глаз, стоя на
остановке, и пара, которой не существует, — признак описки, а не редкой машины.

Разобрано по ячейкам исходного `.docx`. В плоском тексте подписи «Таких нет»
отрываются от своих клеток, и пары пришлось бы угадывать — а зеркальная
ошибка здесь уходит заказчику правдоподобной.

Модуль ничего не правит. Он называет расхождение, а решает человек: кто ошибся,
оператор в виде или оператор в размере, отсюда не видно, и молча менять чужую
графу по догадке нельзя (решение 087, графа S).

Числу дверей тут места нет: дверь → размер живёт в `model.DOORS_BY_SIZE` и
`reconcile.size_from_doors`, и толковать одно и то же дважды — верный способ
истолковать по-разному.
"""

from __future__ import annotations

from .model import VehicleKind, VehicleSize

# Строка автобуса таблицы 3. «Развозка» и «Шаттл» читаются по ней же: это
# назначение рейса, а не кузов, и в таблице 3 их нет вовсе. Прочтение наше, не
# буква инструкции, — потому и названо здесь вслух.
_BUS_SIZES = frozenset({VehicleSize.SMALL, VehicleSize.MEDIUM,
                         VehicleSize.LARGE, VehicleSize.XLARGE})

SIZES_BY_KIND: dict[VehicleKind, frozenset[VehicleSize]] = {
    VehicleKind.BUS: _BUS_SIZES,
    VehicleKind.SHUTTLE_WORK: _BUS_SIZES,
    VehicleKind.SHUTTLE_MALL: _BUS_SIZES,
    # Трамвай меряется вагонами, а не дверьми.
    VehicleKind.TRAM: frozenset({VehicleSize.ONE_CAR, VehicleSize.TWO_CARS,
                                  VehicleSize.THREE_CARS}),
    # У троллейбуса «Малый» помечен «Таких нет», «Средний» — «Таких нет в СПб».
    VehicleKind.TROLLEY: frozenset({VehicleSize.LARGE, VehicleSize.XLARGE}),
    # У маршрутки «Таких нет в СПб» стоит на «Большом» и «Особо большом».
    VehicleKind.MINIBUS: frozenset({VehicleSize.SMALL, VehicleSize.MEDIUM}),
}


def pair_note(kind: VehicleKind | None,
               size: VehicleSize | None) -> str | None:
    """Расхождение пары с таблицей 3 — текст для графы S. `None` — пара обычная.

    Незаполненная половина пары суждению не подлежит: сравнивать не с чем, а
    догадка здесь хуже молчания.
    """
    if kind is None or size is None:
        return None
    allowed = SIZES_BY_KIND.get(kind)
    if allowed is None or size in allowed:
        return None
    return (f"по таблице 3 пары «{kind.value} + {size.value}» не бывает: "
             f"{kind.value} бывает "
             + ", ".join(sorted(s.value for s in allowed)))


def kinds_for_size(size: VehicleSize) -> frozenset[VehicleKind]:
    """Виды, которым такой размер разрешён, — обратная сторона таблицы.

    Нужна там, где размер измерен нами (по дверям), а вид взят у оператора:
    три двери сужают вид до автобуса и троллейбуса, и «Маршрутка» в такой
    строке спорна независимо от того, чему верить.
    """
    return frozenset(kind for kind, sizes in SIZES_BY_KIND.items()
                      if size in sizes)
