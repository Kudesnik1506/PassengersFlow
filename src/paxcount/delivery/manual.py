"""Ручной ввод заказчика: он главнее нашего расчёта.

Сборка заполняет книгу **однажды**. Дальше её ведёт заказчик, и всякая клетка,
которую он тронул, принадлежит ему: пересборка её не правит, а если наше число
с ней расходится — говорит об этом в графе S, оставив значение как есть. Так
решил заказчик, отменив «наше значение главнее» из решения 079.

Отличить правку человека от нашего же прошлого значения по одной книге нельзя:
в клетке лежит текст, а не его происхождение. Поэтому правка ищется **разницей
с эталоном** — копией того, что записала прошлая сборка (она же лежит в
`out/`). Чем книга разошлась с копией, то вписал человек.

Найденная правка закрепляется в реестре и живёт уже там. Эталон нужен только
чтобы заметить новую: как только сборка запишет правку обратно в книгу, книга
и копия сравняются, и разницы не останется — а помнить надо навсегда.

Снять закрепление может только заказчик, ключом при запуске. Молчаливой
перезаписи не бывает: он вписал — значит так верно.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from pathlib import Path

from .fill import COLUMNS, DATE_COLUMN, EXCEL_EPOCH, EXTRA_COLUMNS
from .model import DeliveryRow
from .xlsx import row_values

# Приметы строки, по которым она узнаётся в прошлой книге. Номер строки для
# этого не годится: машина, пропущенная оператором, вписывается по времени
# (решение 069), и после вставки тот же номер указывает на соседнюю машину.
#
# В приметы входит ТОЛЬКО то, что пишем мы сами. Маршрут отсюда убран, и это не
# мелочь: заказчик вписал его в строку, где мы маршрут не опознали, — строка
# перестала узнаваться, и ручной ввод, ради которого перенос и заведён, ломал
# сам перенос. Время и номер ТС ставим мы, и человеку там дописывать нечего.
_ROW_KEY = ("C", "D", "G")

# Короткие имена граф для графы расхождений. Заголовки заказчика туда не
# годятся: «Название видеофайла. Скопировать сюда» в строке спора читается как
# инструкция, а не как имя графы.
TITLES = {
    "A": "группа", "B": "дата", "C": "часы", "D": "минуты", "E": "номер ОП",
    "F": "вид ТС", "G": "номер ТС", "H": "маршрут", "I": "наполненность",
    "J": "размер ТС", "K": "вышло", "L": "зашло", "M": "комментарий",
    "N": "видеофайл", "O": "расшифровщик",
}


@dataclass(frozen=True)
class Edit:
    """Клетка, которую заказчик заполнил или стёр своей рукой.

    Значение — то, что он там оставил; пустая строка значит «стёр намеренно»,
    и это тоже его решение, а не пропуск.
    """

    key: tuple[str, ...]
    repeat: int
    column: str
    value: str


@dataclass(frozen=True)
class Diff:
    """Чем книга заказчика разошлась с тем, что в неё записала сборка."""

    edits: list[Edit]
    spoiled: dict[str, int] = field(default_factory=dict)
    unknown: list[tuple[str, ...]] = field(default_factory=list)


@dataclass(frozen=True)
class Applied:
    """Правки, разложенные по строкам новой сборки.

    `values` — что положить в клетки, `rows` — те же строки с приписками в
    графе расхождений, `lost` — правки, которым не нашлось строки.
    """

    values: dict[int, dict[str, str]]
    rows: list[DeliveryRow]
    lost: list[Edit]


def _plain(value: str) -> str:
    """Значение для сравнения примет. Часы «02» и «2» — одно и то же время.

    Книга приходит и от нас, и из-под Excel, а он свободен показать час и
    двузначным. Приметы, различающие такие записи, разошлись бы на ровном
    месте, и перенос молча потерял бы строку.
    """
    text = (value or "").strip()
    return text.lstrip("0") or "0" if text.isdigit() else text


# Чем узнаётся строка, у которой номера ТС нет вовсе. Такие строки появились
# вместе с цепочкой по К1: машину видели, а прочесть не смогли. Две безымянные
# строки в одну минуту дали бы одинаковые приметы, и перенос правок заказчика
# начал бы путать их — на всей книге, а не только на них.
#
# Графа P (камера и её часы) годится в приметы по той же причине, что и
# остальные три: её пишем мы, и человеку там дописывать нечего.
_NAMELESS_KEY = "P"


def key_of_cells(cell: dict[str, str]) -> tuple[str, ...]:
    """Приметы строки. Без номера ТС к ним добавляется камера с её часами.

    Прежние приметы не трогаются: реестр правок собран на трёх графах, и
    расширь мы их всем строкам — ни одна записанная правка не нашла бы клетки.
    """
    marks = tuple(_plain(cell.get(c) or "") for c in _ROW_KEY)
    if marks[_ROW_KEY.index("G")]:
        return marks
    return marks + (_plain(cell.get(_NAMELESS_KEY) or ""),)


def _key_of_row(row: DeliveryRow) -> tuple[str, ...]:
    """Приметы строки — из тех же граф, что и у клеток: список один на обоих.

    Собирать их по полям модели значило бы держать две копии `_ROW_KEY`, и
    сузив одну, я уже разошёлся со второй: приметы перестали совпадать вовсе,
    а выглядело это как «строка не нашлась».
    """
    return key_of_cells(dict(zip(COLUMNS, row_values(row))))


def _places(rows: list[DeliveryRow]) -> dict[tuple, list[int]]:
    wanted: dict[tuple, list[int]] = {}
    for index, row in enumerate(rows, start=2):
        wanted.setdefault(_key_of_row(row), []).append(index)
    return wanted


def _as_date(value: str) -> date | None:
    """Дата, как её ни записали: числом Excel, «10.09.2026» или строкой JS.

    Книга возвращается от заказчика в том виде, в каком её сохранил его
    редактор, и дата приходит то числом, то текстом. Сравнивать их побуквенно
    значит объявить правкой всю графу разом.
    """
    text = (value or "").strip()
    if not text:
        return None
    if re.fullmatch(r"\d{2}\.\d{2}\.\d{4}", text):
        return datetime.strptime(text, "%d.%m.%Y").date()
    if re.fullmatch(r"\d+(\.0+)?", text):
        days = int(float(text))
        # Только правдоподобный век: маршрут «225» и номер ОП числами тоже
        # бывают, и принимать их за даты нельзя.
        return EXCEL_EPOCH + timedelta(days=days) if 40000 <= days <= 60000 else None
    js = re.match(r"[A-Za-z]{3} ([A-Za-z]{3}) (\d{1,2}) (\d{4})", text)
    if js:
        try:
            return datetime.strptime(" ".join(js.groups()), "%b %d %Y").date()
        except ValueError:
            return None
    return None


def found(book: list[dict[str, str]],
           baseline: list[dict[str, str]]) -> Diff:
    """Правки заказчика: чем книга разошлась с нашей копией той же книги.

    С НАШИМ НОВЫМ значением книга не сравнивается вовсе. Иначе всякое
    улучшение расчёта выглядело бы правкой человека и закреплялось бы навсегда
    — ровно то, чего боялось решение 079.
    """
    was = _cells_by_key(baseline)
    edits: list[Edit] = []
    spoiled: dict[str, int] = {}
    unknown: list[tuple[str, ...]] = []
    seen: dict[tuple, int] = {}
    for cell in book:
        key = key_of_cells(cell)
        repeat = seen.get(key, 0)
        seen[key] = repeat + 1
        places = was.get(key, [])
        if repeat >= len(places):
            unknown.append(key)
            continue
        before = places[repeat]
        for column in COLUMNS:
            if column in EXTRA_COLUMNS or column in _ROW_KEY:
                continue
            theirs = (cell.get(column) or "").strip()
            ours = (before.get(column) or "").strip()
            if theirs == ours or _plain(theirs) == _plain(ours):
                continue
            if column == DATE_COLUMN:
                # Редактор, сохранивший книгу не по-Excel, кладёт в дату строку
                # JS вместо числа. Дата та же — испорчено представление, и это
                # не правка: закрепив её, мы навсегда положим заказчику текст
                # вместо даты, и его сводная перестанет считать по датам.
                mine, yours = _as_date(ours), _as_date(theirs)
                if yours is None or mine == yours:
                    spoiled[column] = spoiled.get(column, 0) + 1
                    continue
            edits.append(Edit(key=key, repeat=repeat, column=column, value=theirs))
    return Diff(edits=edits, spoiled=spoiled, unknown=unknown)


def theirs(book: list[dict[str, str]],
            baseline: list[dict[str, str]],
            rows: list[DeliveryRow]) -> Diff:
    """Всё в книге, что принадлежит заказчику, — по двум разным признакам.

    Первый признак — разница с эталоном: клетка, которую заполняем мы, стала
    другой, значит там побывал человек (`found`).

    Второй — графа, которой наш расчёт не заполняет вовсе: наполненность
    (решение 032), «вышло» и «зашло» (запрет 13). Там человек невидим для
    сравнения: значение в книге и в эталоне одно и то же, потому что эталон
    получил его прошлым переносом. Боевая потеря на трёх строках: заказчик
    проставил наполненность, сборка стёрла её — разница молчала.

    Поэтому такая клетка закрепляется по происхождению, а не по различию:
    графу заполняет не наш расчёт, и значит она его. Обратное правило —
    закреплять всё уцелевшее — заморозило бы наш расчёт навсегда (решение 079),
    и потому первый признак по-прежнему судит наши графы.
    """
    diff = found(book, baseline)
    return Diff(edits=merged(carried(book, rows), diff.edits),
                 spoiled=diff.spoiled, unknown=diff.unknown)


def _cells_by_key(rows: list[dict[str, str]]) -> dict[tuple, list[dict[str, str]]]:
    by_key: dict[tuple, list[dict[str, str]]] = {}
    for cell in rows:
        by_key.setdefault(key_of_cells(cell), []).append(cell)
    return by_key


def merged(kept: list[Edit], fresh: list[Edit]) -> list[Edit]:
    """Реестр плюс свежие правки: последнее слово заказчика вытесняет прежнее."""
    order = [(e.key, e.repeat, e.column) for e in kept]
    by_cell = {cell: e for cell, e in zip(order, kept)}
    for edit in fresh:
        cell = (edit.key, edit.repeat, edit.column)
        if cell not in by_cell:
            order.append(cell)
        by_cell[cell] = edit
    return [by_cell[cell] for cell in order]


def applied(rows: list[DeliveryRow], edits: list[Edit]) -> Applied:
    """Кладёт правки поверх нашего, а наше несогласие — в графу расхождений."""
    places = {key: list(indexes) for key, indexes in _places(rows).items()}
    values: dict[int, dict[str, str]] = {}
    notes: dict[int, dict[str, str]] = {}
    lost: list[Edit] = []
    for edit in edits:
        indexes = places.get(edit.key) or []
        if edit.repeat >= len(indexes):
            lost.append(edit)
            continue
        index = indexes[edit.repeat]
        ours = dict(zip(COLUMNS, row_values(rows[index - 2])))
        mine = (ours.get(edit.column) or "").strip()
        values.setdefault(index, {})[edit.column] = edit.value
        # Спор бывает только там, где нам есть что сказать. Клетку, которую мы
        # не заполняем вовсе («вышло» и «зашло», запрет 13), правка заказчика
        # не оспаривает — а «у нас пусто» в графе расхождений залило бы её
        # целиком и обесценило (§11 КНИГА.md).
        if mine and mine != edit.value.strip():
            notes.setdefault(index, {})[f"{TITLES[edit.column]}, ваша правка"] = (
                f"у нас {mine}, оставлено ваше {edit.value or 'пусто'}"
            )
    said = [
        row.model_copy(update={"disagreements": {**row.disagreements, **note}})
        if (note := notes.get(index)) else row
        for index, row in enumerate(rows, start=2)
    ]
    return Applied(values=values, rows=said, lost=lost)


def unmarked(highlight: dict[int, set[str]],
              values: dict[int, dict[str, str]]) -> dict[int, set[str]]:
    """Снимает жёлтое с клеток, где стоит правка заказчика.

    Жёлтое — наше (запрет 11), а нашего в такой клетке больше нет: значение
    там его, и наш спор ушёл в графу S.
    """
    left: dict[int, set[str]] = {}
    for index, columns in highlight.items():
        rest = {c for c in columns if c not in values.get(index, {})}
        if rest:
            left[index] = rest
    return left


def without(edits: list[Edit], refs: list[str],
             rows: list[DeliveryRow]) -> list[Edit]:
    """Снимает закрепление с названных клеток — «H4» значит графа H, строка 4.

    Промах не прощается: «сняли ноль правок» выглядит как успех, и заказчик
    узнал бы о своей описке, только увидев в книге затёртое место.
    """
    places = _places(rows)
    where = {(index, column): (key, repeat)
              for key, indexes in places.items()
              for repeat, index in enumerate(indexes)
              for column in COLUMNS}
    drop = set()
    for ref in refs:
        match = re.fullmatch(r"([A-Za-z]+)(\d+)", ref.strip())
        if match is None:
            raise LookupError(f"{ref}: клетка называется буквой графы и номером строки")
        column, index = match.group(1).upper(), int(match.group(2))
        cell = where.get((index, column))
        if cell is None:
            raise LookupError(f"{ref}: в книге нет такой клетки")
        key, repeat = cell
        if not any(e.key == key and e.repeat == repeat and e.column == column
                    for e in edits):
            raise LookupError(f"{ref}: правки заказчика в этой клетке нет")
        drop.add((key, repeat, column))
    return [e for e in edits if (e.key, e.repeat, e.column) not in drop]


def save(path: Path, edits: list[Edit]) -> Path:
    """Реестр правок рядом с книгой. Заказчик его читает, а не только мы."""
    path.parent.mkdir(parents=True, exist_ok=True)
    body = {"правки": [{"приметы": list(e.key), "повтор": e.repeat,
                         "графа": e.column, "значение": e.value} for e in edits]}
    path.write_text(json.dumps(body, ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def load(path: Path) -> list[Edit]:
    if not path.exists():
        return []
    body = json.loads(path.read_text(encoding="utf-8"))
    return [Edit(key=tuple(item["приметы"]), repeat=item["повтор"],
                  column=item["графа"], value=item["значение"])
             for item in body.get("правки", [])]
def orphaned(previous: list[dict[str, str]],
              rows: list[DeliveryRow]) -> list[dict[str, str]]:
    """Строки прошлой книги, которым в новой не нашлось места, — с их данными.

    Перенос идёт по приметам, и приметы могут смениться: портал подставил
    госномер вместо бортового, оператор исправил маршрут. Тогда ручной ввод
    пропадёт так же, как при слепой перезаписи, — только теперь незаметно и
    для нас. Поэтому такие строки называются поимённо, а не молча теряются.

    Строка без данных сверх самих примет сюда не попадает: терять в ней нечего.
    """
    wanted = _places(rows)
    lost: list[dict[str, str]] = []
    for cell in previous:
        places = wanted.get(key_of_cells(cell))
        if places:
            places.pop(0)
            continue
        kept = {c: (v or "").strip() for c, v in cell.items()
                if c in COLUMNS and (v or "").strip()}
        if set(kept) - set(_ROW_KEY):
            lost.append(kept)
    return lost


def carried_over(previous: list[dict[str, str]],
                  rows: list[DeliveryRow]) -> dict[int, dict[str, str]]:
    """Запасной путь: клетки прошлой книги, которые наша сборка не заполняет.

    Годится, когда эталона нет и правку заказчика распознать нечем — первая
    сборка на новом месте, потерянная копия. Тогда о заполненных нами графах
    сказать нельзя ничего, а пустые у нас клетки заведомо его: наполненность
    по прибытию мы не считаем вовсе, и графу I он заполняет руками по записи.

    В обычном прогоне работает не это, а реестр правок: там главнее заказчик,
    и его значение ложится поверх нашего (`applied`).

    Наши собственные графы (P, Q, R, S) не переносятся вовсе. Они существуют
    только потому, что мы их завели, и пустота в них — наше утверждение
    «сказать нечего», а не пробел, который кто-то забыл заполнить. Боевая
    сборка показала цену недоразумения: устаревшие расхождения вернулись из
    прошлой книги в 292 строки из 295 и остались бы там навсегда.

    Строка ищется по приметам (`_ROW_KEY`), а не по номеру. Одинаковые приметы
    бывают — повторное нажатие оператора даёт две строки в ту же минуту
    (решение 075), — и разбираются по порядку следования.
    """
    wanted = _places(rows)
    produced = {index: row_values(row) for index, row in enumerate(rows, start=2)}

    kept: dict[int, dict[str, str]] = {}
    for cell in previous:
        places = wanted.get(key_of_cells(cell))
        if not places:
            continue
        index = places.pop(0)
        ours = dict(zip(COLUMNS, produced[index]))
        keep = {column: (value or "").strip()
                for column, value in cell.items()
                if column in COLUMNS and column not in EXTRA_COLUMNS
                and (value or "").strip()
                and not (ours.get(column) or "").strip()}
        if keep:
            kept[index] = keep
    return kept



def carried(previous: list[dict[str, str]],
             rows: list[DeliveryRow]) -> list[Edit]:
    """Запасной путь без эталона: перенос пустых клеток — тоже правки заказчика.

    Реестр заводится один раз, и завести его надо даже там, где сравнивать не
    с чем: иначе первая же сборка на новом месте потеряет «вышло» и «зашло».
    """
    where = {index: (key, repeat)
              for key, indexes in _places(rows).items()
              for repeat, index in enumerate(indexes)}
    return [Edit(key=where[index][0], repeat=where[index][1],
                  column=column, value=value)
             for index, columns in sorted(carried_over(previous, rows).items())
             for column, value in sorted(columns.items())]
