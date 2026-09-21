"""Раскладка строк книги — правка заказчика наравне с клеткой.

Реестр правок (`manual`) держит значения клеток. Этого мало: заказчик удаляет
розовые повторы, переставляет машины, которые пришли в другом порядке,
дописывает номер в строку, где мы его не прочли, и заводит строку на машину,
которую не видели ни оператор, ни мы. Ни одно из этих действий не значение
клетки, и прежде каждая пересборка молча откатывала их все: вернула пятнадцать
удалённых повторов и прежний порядок в 08:13.

Правка раскладки узнаётся тем же способом, что и правка клетки, — разницей с
эталоном, копией прошлой сборки. Порядок строк книги выравнивается с порядком
эталона по приметам; что осталось без пары, то и сделал человек:

* строка эталона без пары — **удалена**;
* строка книги без пары — **добавлена** (стоит после своей соседки сверху);
* та же строка без пары с обеих сторон — **переставлена**;
* пара без общих примет, но с теми же часами, минутами и камерой (графа P) —
  **переименована**: заказчик вписал номер в строку, где его не было.

Найденное закрепляется в том же реестре, что и правки клеток, и повторяется
на каждой пересборке: после записи книга и эталон сравняются, и разница
больше ничего не покажет.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field, replace
from datetime import date, datetime, time
from difflib import SequenceMatcher
from pathlib import Path

from .fill import COLUMNS, DATE_COLUMN, EXTRA_COLUMNS
from .manual import (
    _NAMELESS_KEY, _ROW_KEY, Edit, _key_of_row, _plain, key_of_cells,
)
from .model import KINDS_NEEDING_STATE_NUMBER, DeliveryRow, VehicleKind
from .sequence import Entry

# Строка, которую заказчик завёл сам: её не нажимал оператор и не находили мы.
CUSTOMER = "заказчик"

# Место строки: приметы и порядковый номер среди строк с теми же приметами.
# Одинаковые приметы бывают — повторное нажатие в ту же минуту (решение 075).
Place = tuple[tuple[str, ...], int]

# Графы, которые в новой строке заказчика — его правки. Группа, дата и номер
# ОП ставятся сборкой одинаково для всей книги, а приметы — остов строки.
_NOT_EDITS = set(_ROW_KEY) | set(EXTRA_COLUMNS) | {"A", "B", "E"}


@dataclass(frozen=True)
class Layout:
    """Правки раскладки. `edits` — клетки переименованных и новых строк.

    Их правки нельзя найти обычной разницей с эталоном: приметы строки
    сменились, и искать её прежнюю копию не по чему. Поэтому они собираются
    здесь, при самом распознавании, и уходят в общий реестр клеток.
    """

    deleted: tuple[Place, ...] = ()
    renamed: tuple[tuple[Place, dict], ...] = ()
    added: tuple[tuple[Place, Place | None, dict], ...] = ()
    moved: tuple[tuple[Place, Place | None], ...] = ()
    edits: tuple[Edit, ...] = field(default=(), compare=False)

    @property
    def empty(self) -> bool:
        return not (self.deleted or self.renamed or self.added or self.moved)

    @property
    def explained(self) -> set[tuple[str, ...]]:
        """Приметы строк книги, чьё отсутствие в эталоне объяснено здесь."""
        return ({key_of_cells(cells) for _, cells in self.renamed}
                | {place[0] for place, _, _ in self.added})


@dataclass(frozen=True)
class Result:
    entries: list[Entry]
    missed: list[str]


def _places(keys: list[tuple[str, ...]]) -> list[Place]:
    seen: dict[tuple[str, ...], int] = {}
    out: list[Place] = []
    for key in keys:
        out.append((key, seen.get(key, 0)))
        seen[key] = seen.get(key, 0) + 1
    return out


def _clean(cell: dict[str, str]) -> dict[str, str]:
    return {c: (cell.get(c) or "").strip() for c in COLUMNS if (cell.get(c) or "").strip()}


def _same_moment(a: dict[str, str], b: dict[str, str]) -> bool:
    """Та же ли это строка, хотя номер в ней сменился: часы, минуты, камера."""
    return (all(_plain(a.get(c) or "") == _plain(b.get(c) or "") for c in ("C", "D"))
            and (a.get(_NAMELESS_KEY) or "").strip() == (b.get(_NAMELESS_KEY) or "").strip()
            and bool((a.get(_NAMELESS_KEY) or "").strip()))


def found(book: list[dict[str, str]], baseline: list[dict[str, str]]) -> Layout:
    """Правки раскладки: чем порядок строк книги разошёлся с эталоном."""
    was = _places([key_of_cells(c) for c in baseline])
    now = _places([key_of_cells(c) for c in book])
    gone: list[int] = []
    new: list[int] = []
    for tag, i1, i2, j1, j2 in SequenceMatcher(a=was, b=now, autojunk=False).get_opcodes():
        if tag != "equal":
            gone += range(i1, i2)
            new += range(j1, j2)

    # Переставленная: одна и та же строка ушла с одного места и встала на другое.
    left = {was[i] for i in gone}
    moved = [(now[j], now[j - 1] if j else None) for j in new if now[j] in left]
    shifted = {place for place, _ in moved}
    gone = [i for i in gone if was[i] not in shifted]
    new = [j for j in new if now[j] not in shifted]

    renamed: list[tuple[Place, dict]] = []
    edits: list[Edit] = []
    for j in list(new):
        pair = next((i for i in gone if _same_moment(baseline[i], book[j])), None)
        if pair is None:
            continue
        gone.remove(pair)
        new.remove(j)
        renamed.append((was[pair], _clean(book[j])))
        for column in COLUMNS:
            if column in _NOT_EDITS or column == DATE_COLUMN:
                continue
            theirs = (book[j].get(column) or "").strip()
            ours = (baseline[pair].get(column) or "").strip()
            if theirs != ours:
                edits.append(Edit(key=now[j][0], repeat=now[j][1],
                                   column=column, value=theirs))

    added: list[tuple[Place, Place | None, dict]] = []
    for j in new:
        cells = _clean(book[j])
        added.append((now[j], now[j - 1] if j else None, cells))
        # Все графы заказчика, и пустые тоже: строка целиком его, и пустая
        # клетка в ней — его решение, а не место для нашего кода.
        edits += [Edit(key=now[j][0], repeat=now[j][1], column=c,
                        value=cells.get(c, ""))
                  for c in COLUMNS if c not in _NOT_EDITS and c <= "M"]

    return Layout(deleted=tuple(was[i] for i in gone), renamed=tuple(renamed),
                  added=tuple(added), moved=tuple(moved), edits=tuple(edits))


def merged(kept: Layout, fresh: Layout) -> Layout:
    """Реестр плюс свежие правки раскладки: последнее слово — свежее."""
    renamed = {place: cells for place, cells in kept.renamed}
    renamed.update({place: cells for place, cells in fresh.renamed})
    added = {place: (after, cells) for place, after, cells in kept.added}
    added.update({place: (after, cells) for place, after, cells in fresh.added})
    moved = {place: after for place, after in kept.moved}
    moved.update({place: after for place, after in fresh.moved})
    return Layout(
        deleted=tuple(dict.fromkeys(kept.deleted + fresh.deleted)),
        renamed=tuple(renamed.items()),
        added=tuple((place, after, cells) for place, (after, cells) in added.items()),
        moved=tuple(moved.items()),
        edits=fresh.edits,
    )


def surviving(edits: list[Edit], plan: Layout) -> list[Edit]:
    """Правки клеток без тех, чью строку заказчик удалил сам.

    Держаться им не за что, и считать их потерянными нельзя: реестр
    отказывается писать книгу, если правка потеряла строку (рубеж 2), и
    удалённый повтор заблокировал бы книгу навсегда.
    """
    gone = set(plan.deleted)
    return [e for e in edits if (e.key, e.repeat) not in gone]


def _where(entries: list[Entry]) -> dict[Place, int]:
    return {place: n for n, place in
             enumerate(_places([_key_of_row(e.row) for e in entries]))}


def _numbered(row: DeliveryRow, number: str | None) -> dict:
    """Номер из графы G — в госномер или бортовой, смотря по виду ТС."""
    if row.kind in KINDS_NEEDING_STATE_NUMBER:
        return {"state_number": number}
    return {"board_number": number}


def _int(text: str | None) -> int | None:
    text = (text or "").strip()
    return int(float(text)) if text.replace(".", "", 1).isdigit() else None


def _renamed(row: DeliveryRow, cells: dict) -> DeliveryRow:
    """Строка с приметами, которые вписал заказчик, — и с его маршрутом.

    Маршрут входит в строку, а не только ложится правкой поверх: без него
    строка внутри сборки остаётся неопознанной, и сборка припишет в
    комментарий «маршрут не опознан» там, где заказчик маршрут уже вписал.
    """
    update = _numbered(row, cells.get("G") or None)
    route = (cells.get("H") or "").strip()
    if route and route.upper() != "N/A":
        update["route"] = route
    if (hours := _int(cells.get("C"))) is not None:
        update["hours"] = hours
    if (minutes := _int(cells.get("D"))) is not None:
        update["minutes"] = minutes
    return row.model_copy(update=update)


def _built(cells: dict, *, day: date, group: str, stop: str,
            operator: str) -> Entry | None:
    try:
        kind = VehicleKind((cells.get("F") or "").strip())
    except ValueError:
        return None
    hours, minutes = _int(cells.get("C")), _int(cells.get("D"))
    if hours is None or minutes is None:
        return None
    row = DeliveryRow(group=group, date=day.strftime("%d.%m.%Y"), hours=hours,
                       minutes=minutes, stop=stop, kind=kind,
                       route=cells.get("H") or None, operator=operator)
    row = row.model_copy(update=_numbered(row, cells.get("G") or None))
    return Entry(moment=datetime.combine(day, time(hours, minutes)), row=row,
                 origin=CUSTOMER)


def _after(entries: list[Entry], after: Place | None, item: Entry) -> list[Entry]:
    """Вставка после соседки сверху; нет её — по времени."""
    if after is None:
        return [item, *entries]
    at = _where(entries).get(after)
    if at is None:
        at = next((n for n, e in enumerate(entries) if e.moment > item.moment),
                  len(entries)) - 1
    return [*entries[:at + 1], item, *entries[at + 1:]]


def apply(entries: list[Entry], plan: Layout, *, day: date, group: str,
          stop: str, operator: str) -> Result:
    """Свежая лента в раскладке заказчика.

    Порядок шагов не случаен: переименование первым — удаление и соседки
    новых строк ищутся уже по его приметам; новые строки раньше перестановок —
    переставленная может стоять после строки, которую завёл заказчик.
    """
    items = list(entries)
    missed: list[str] = []

    for place, cells in plan.renamed:
        at = _where(items).get(place)
        if at is None:
            missed.append(f"переименованная строка {'/'.join(place[0])} не нашлась")
            continue
        items[at] = replace(items[at], row=_renamed(items[at].row, cells))

    where = _where(items)
    drop = set()
    for place in plan.deleted:
        if place in where:
            drop.add(where[place])
    items = [e for n, e in enumerate(items) if n not in drop]

    for place, after, cells in plan.added:
        if place in _where(items):
            continue
        item = _built(cells, day=day, group=group, stop=stop, operator=operator)
        if item is None:
            missed.append(f"строку заказчика {'/'.join(place[0])} не собрать: "
                           "нет вида ТС или времени")
            continue
        items = _after(items, after, item)

    for place, after in plan.moved:
        at = _where(items).get(place)
        if at is None:
            missed.append(f"переставленная строка {'/'.join(place[0])} не нашлась")
            continue
        item = items.pop(at)
        items = _after(items, after, item)

    return Result(entries=items, missed=missed)


def _place_out(place: Place | None):
    return None if place is None else {"приметы": list(place[0]), "повтор": place[1]}


def _place_in(body) -> Place | None:
    return None if body is None else (tuple(body["приметы"]), body["повтор"])


SECTION = "строки"


def save(path: Path, plan: Layout) -> Path:
    """Раскладка — в тот же реестр, рядом с правками клеток, не затирая их."""
    body = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    body[SECTION] = {
        "удалены": [_place_out(p) for p in plan.deleted],
        "переименованы": [{"место": _place_out(p), "клетки": c} for p, c in plan.renamed],
        "добавлены": [{"место": _place_out(p), "после": _place_out(a), "клетки": c}
                       for p, a, c in plan.added],
        "перемещены": [{"место": _place_out(p), "после": _place_out(a)}
                        for p, a in plan.moved],
    }
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(path: Path) -> Layout:
    if not path.exists():
        return Layout()
    body = json.loads(path.read_text(encoding="utf-8")).get(SECTION) or {}
    return Layout(
        deleted=tuple(_place_in(p) for p in body.get("удалены", [])),
        renamed=tuple((_place_in(x["место"]), x["клетки"]) for x in body.get("переименованы", [])),
        added=tuple((_place_in(x["место"]), _place_in(x["после"]), x["клетки"])
                    for x in body.get("добавлены", [])),
        moved=tuple((_place_in(x["место"]), _place_in(x["после"]))
                    for x in body.get("перемещены", [])),
    )
