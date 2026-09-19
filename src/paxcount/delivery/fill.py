"""Заполнение книги ПО ШАБЛОНУ заказчика: подменяется только лист «Бланк».

`xlsx.write_rows` собирает книгу с нуля и пишет всё встроенными строками. Для
нашей рабочей выгрузки это правильно — меньше кода и никакой зависимости. Для
файла, который уходит заказчику, этого мало, и разница видна в принятом им
файле: группа, дата, часы, минуты, маршрут, «вышло» и «зашло» лежат там
ЧИСЛОВЫМИ ячейками. Текст «3» в графе «Вышло» Excel не суммирует — сводная
заказчика покажет ноль, и ошибка будет молчаливой.

Шаблон несёт и то, чего мы сами не построим: выпадающие списки на «Тип ТС»,
«Размер ТС» и «Наполненность», привязанные к служебному листу. Собрав книгу
заново, мы их теряем.

Поэтому здесь берётся шаблон заказчика и в нём заменяется содержимое одного
листа. Всё остальное — стили, служебные листы, списки, печать — переносится
байт в байт.

Индексы стилей взяты из принятого заказчиком файла: он сделан из этого же
шаблона, и номера в `cellXfs` совпадают. Числовой формат даты — `numFmtId=14`
(ДД.ММ.ГГГГ), стиль 4.
"""

from __future__ import annotations

import re
import shutil
import zipfile
from datetime import date, datetime
from pathlib import Path
from xml.sax.saxutils import escape

from .model import DeliveryRow
from .xlsx import BLANK_SHEET, EXTRA_HEADERS, NA, row_values

# Ноль отсчёта дат Excel. 1899-12-30, а не 1900-01-01: в отсчёте есть
# несуществующее 29 февраля 1900 года, и сдвиг на два дня — часть формата.
EXCEL_EPOCH = date(1899, 12, 30)

# Пятнадцать граф заказчика (A–O) и четыре наши (P–S): часы камеры, наш счёт
# двумя графами и расхождения (`xlsx.EXTRA_HEADERS`).
COLUMNS = "ABCDEFGHIJKLMNOPQRS"
EXTRA_COLUMNS = "PQRS"
# Стиль на графу — по принятому файлу заказчика. Нашей графе достаётся стиль
# соседней текстовой: она в принятом файле не описана вовсе.
STYLES = {"A": 3, "B": 4, "C": 1, "D": 1, "E": 3, "F": 3, "G": 1, "H": 1,
           "I": 1, "J": 1, "K": 1, "L": 1, "M": 5, "N": 3, "O": 3, "P": 3,
           "Q": 1, "R": 1, "S": 5}
# Графы, которые заказчик держит числами. Графа M числовая, только пока в ней
# один код: с примечанием словами она становится текстом, это решает проверка
# ниже. Остальные — строки, даже если в них
# цифры: номер ОП «1715-15182» числом не является, и превращать его нельзя.
# K и L сюда больше не входят: сборка их не заполняет вовсе, а заказчик впишет
# число сам. Наш счёт держится числами в Q и R — текст сводная не сложит.
NUMERIC = set("ABCDHMQR")
DATE_COLUMN = "B"
# Заливка для клеток, разошедшихся с выгрузкой оператора. Шаблон заказчика
# заливок не содержит вовсе, поэтому стиль добавляется нами — копией нынешнего
# стиля графы плюс заливка. Копией, а не новым стилем: иначе помеченная клетка
# потеряет выравнивание и формат даты, и видно станет не расхождение, а правку.
MISMATCH_COLOR = "FFFFE699"
# Заливка для повторных нажатий оператора. Цвет отдельный, потому что это
# другое сообщение: не спор о числе, а строка, которую мы считаем лишней
# (решение 075). Одним цветом с расхождением они сливаются, и читатель книги
# не отличит «мы посчитали иначе» от «эту машину записали дважды».
DUPLICATE_COLOR = "FFFFC7CE"


def _serial(text: str) -> int:
    return (datetime.strptime(text, "%d.%m.%Y").date() - EXCEL_EPOCH).days


def _cell(ref: str, column: str, value: str, style_index: int,
           marked: bool = False) -> str:
    style = f' s="{style_index}"'
    if not value:
        # Пустая графа, разошедшаяся с оператором, — самый интересный случай:
        # он знал, а мы нет. Без клетки пометке не на чем держаться.
        return f"<c r=\"{ref}\"{style}/>" if marked else ""
    if column == DATE_COLUMN:
        return f'<c r="{ref}"{style}><v>{_serial(value)}</v></c>'
    numeric = column in NUMERIC and value != NA and re.fullmatch(r"-?\d+", value)
    if numeric:
        return f'<c r="{ref}"{style}><v>{value}</v></c>'
    return (f'<c r="{ref}"{style} t="inlineStr"><is><t xml:space="preserve">'
             f"{escape(value)}</t></is></c>")


# Приметы строки, по которым она узнаётся в прошлой книге. Номер строки для
# этого не годится: машина, пропущенная оператором, вписывается по времени
# (решение 069), и после вставки тот же номер указывает на соседнюю машину.
#
# В приметы входит ТОЛЬКО то, что пишем мы сами. Маршрут отсюда убран, и это не
# мелочь: заказчик вписал его в строку, где мы маршрут не опознали, — строка
# перестала узнаваться, и ручной ввод, ради которого перенос и заведён, ломал
# сам перенос. Время и номер ТС ставим мы, и человеку там дописывать нечего.
_ROW_KEY = ("C", "D", "G")


def _plain(value: str) -> str:
    """Значение для сравнения примет. Часы «02» и «2» — одно и то же время.

    Книга приходит и от нас, и из-под Excel, а он свободен показать час и
    двузначным. Приметы, различающие такие записи, разошлись бы на ровном
    месте, и перенос молча потерял бы строку.
    """
    text = (value or "").strip()
    return text.lstrip("0") or "0" if text.isdigit() else text


def _key_of_cells(cell: dict[str, str]) -> tuple[str, ...]:
    return tuple(_plain(cell.get(c) or "") for c in _ROW_KEY)


def _key_of_row(row: DeliveryRow) -> tuple[str, ...]:
    """Приметы строки — из тех же граф, что и у клеток: список один на обоих.

    Собирать их по полям модели значило бы держать две копии `_ROW_KEY`, и
    сузив одну, я уже разошёлся со второй: приметы перестали совпадать вовсе,
    а выглядело это как «строка не нашлась».
    """
    return _key_of_cells(dict(zip(COLUMNS, row_values(row))))


def _places(rows: list[DeliveryRow]) -> dict[tuple, list[int]]:
    wanted: dict[tuple, list[int]] = {}
    for index, row in enumerate(rows, start=2):
        wanted.setdefault(_key_of_row(row), []).append(index)
    return wanted


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
        places = wanted.get(_key_of_cells(cell))
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
    """Клетки прошлой книги, которые наша сборка не заполняет: их не стирают.

    Книга лежит у заказчика, и он в ней ПИШЕТ: наполненность по прибытию мы не
    считаем вовсе, и графу I он заполняет руками по записи. Пересборка обязана
    оставить эту работу на месте — чужой труд не стирается молча.

    Наше значение всегда главнее: клетку, которую заполняем мы, перенос не
    трогает, иначе исправление данных никогда не доехало бы до книги.

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
        places = wanted.get(_key_of_cells(cell))
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


def _row_xml(index: int, values: list[str], marks: dict[str, str],
              marked_style: dict[str, dict[int, int]]) -> str:
    cells = []
    for column, value in zip(COLUMNS, values):
        base = STYLES[column]
        color = marks.get(column)
        style = marked_style.get(color, {}).get(base, base) if color else base
        cells.append(
            _cell(f"{column}{index}", column, value, style, color is not None))
    return f'<row r="{index}">{"".join(cells)}</row>'


def _sheet_target(z: zipfile.ZipFile, name: str) -> str:
    book = z.read("xl/workbook.xml").decode("utf-8")
    match = re.search(rf'<sheet name="{re.escape(name)}"[^>]*r:id="([^"]+)"', book)
    if match is None:
        raise LookupError(f"в шаблоне нет листа {name!r}")
    rels = z.read("xl/_rels/workbook.xml.rels").decode("utf-8")
    target = re.search(rf'Id="{match.group(1)}"[^>]*Target="([^"]+)"', rels)
    if target is None:
        raise LookupError(f"в шаблоне нет связи для листа {name!r}")
    return "xl/" + target.group(1).lstrip("/")


def _with_fills(styles: str,
                 colors: tuple[str, ...]) -> tuple[str, dict[str, dict[int, int]]]:
    """Добавляет по заливке на цвет и по копии каждого стиля под каждую.

    Возвращает карту «цвет → (нынешний стиль графы → стиль с этой заливкой)».
    """
    fills = re.search(r'<fills count="(\d+)">(.*?)</fills>', styles, re.S)
    first = int(fills.group(1))
    painted = "".join(
        f'<fill><patternFill patternType="solid">'
        f'<fgColor rgb="{color}"/><bgColor indexed="64"/></patternFill></fill>'
        for color in colors
    )
    styles = styles.replace(
        fills.group(0),
        f'<fills count="{first + len(colors)}">{fills.group(2)}{painted}</fills>',
        1,
    )
    block = re.search(r'<cellXfs count="(\d+)">(.*?)</cellXfs>', styles, re.S)
    entries = re.findall(r"<xf [^>]*?/>|<xf [^>]*?>.*?</xf>", block.group(2), re.S)
    count = int(block.group(1))
    added: list[str] = []
    mapping: dict[str, dict[int, int]] = {}
    for n, color in enumerate(colors):
        fill_id = first + n
        mapping[color] = {}
        for base in sorted(set(STYLES.values())):
            clone = entries[base]
            # fillId ЗАМЕНЯЕТСЯ, а не дописывается: второй такой же атрибут
            # Excel прочитает по первому, и заливка молча не появится.
            if 'fillId="' in clone:
                clone = re.sub(r'fillId="\d+"', f'fillId="{fill_id}"', clone, count=1)
            else:
                clone = clone.replace("<xf ", f'<xf fillId="{fill_id}" ', 1)
            if 'applyFill="1"' not in clone:
                clone = clone.replace("<xf ", '<xf applyFill="1" ', 1)
            mapping[color][base] = count + len(added)
            added.append(clone)
    styles = styles.replace(
        block.group(0),
        f'<cellXfs count="{count + len(added)}">{block.group(2)}{"".join(added)}</cellXfs>',
        1,
    )
    return styles, mapping


def _filled_sheet(xml: str, rows: list[DeliveryRow],
                   marks: dict[int, dict[str, str]],
                   marked_style: dict[str, dict[int, int]],
                   keep: dict[int, dict[str, str]] | None = None) -> str:
    """Шапка шаблона остаётся своя, ниже ложатся наши строки."""
    keep = keep or {}
    header = re.search(r'<row r="1".*?</row>', xml, re.S)
    body = "".join(
        _row_xml(i, _with_kept(row_values(row), keep.get(i, {})),
                  marks.get(i, {}), marked_style)
        for i, row in enumerate(rows, start=2)
    )
    data = (_titled(header.group(0)) if header else "") + body
    xml = re.sub(r"<sheetData\s*/>|<sheetData>.*?</sheetData>",
                  f"<sheetData>{data}</sheetData>", xml, count=1, flags=re.S)
    return re.sub(r'<dimension ref="A1:[A-Z]+\d+"\s*/>',
                   f'<dimension ref="A1:{COLUMNS[-1]}{len(rows) + 1}"/>',
                   xml, count=1)


def _titled(header: str) -> str:
    """Дописывает подпись нашей графы в шапку заказчика, не трогая его собственную.

    Шапка приходит из шаблона и уходит из него же байт в байт: на той стороне
    книгу разбирают по тексту заголовка. Но наших граф в его шапке нет, а
    столбцы без подписи читаются как мусор справа от таблицы.
    """
    body = header
    for column, title in zip(EXTRA_COLUMNS, EXTRA_HEADERS):
        if f'r="{column}1"' in body:
            continue
        cell = _cell(f"{column}1", column, title, STYLES[column])
        body = body[: body.rindex("</row>")] + cell + "</row>"
    return body


def _with_kept(values: list[str], kept: dict[str, str]) -> list[str]:
    """Наши значения плюс перенесённые: своё не уступаем, чужое не затираем."""
    if not kept:
        return values
    out = list(values)
    for column, value in kept.items():
        position = COLUMNS.index(column)
        if not (out[position] or "").strip():
            out[position] = value
    return out


# Сколько букв имени должно совпасть, чтобы считать замок нашим. Excel у длинных
# имён срезает начало (`~$blitsa_...` для `Tablitsa_...`), поэтому сравнивается
# хвост. Восьми хватает, чтобы не спутать две книги в одной папке, и мало,
# чтобы промахнуться мимо усечённого имени.
_LOCK_TAIL = 8


def opened_by(book: Path) -> str | None:
    """Имя файла-замка, если книга открыта в Excel или LibreOffice. Иначе `None`.

    Сборка заменяет файл целиком, а редактор держит свою копию в памяти и
    подмены не видит. Дальше решает случай: сохранит человек — ляжет поверх
    пересборки, пересоберём мы — ляжет поверх его правок. Оба исхода это
    потеря чужой работы, и ни один не виден в тот момент, когда случается.

    Ошибаться здесь безопаснее в сторону ложной тревоги: лишний отказ стоит
    одной команды, пропущенный замок — чужого дня работы.
    """
    folder = book.parent
    if not folder.is_dir():
        return None
    name = book.name
    tail = name[-_LOCK_TAIL:] if len(name) > _LOCK_TAIL else name
    for other in sorted(folder.iterdir()):
        if other.name.startswith("~$"):
            if name.endswith(other.name[2:]) or other.name[2:].endswith(tail):
                return other.name
        # LibreOffice: `.~lock.<имя>#`
        if other.name == f".~lock.{name}#":
            return other.name
    return None


def fill_template(
    template: Path,
    rows: list[DeliveryRow],
    out: Path,
    sheet: str = BLANK_SHEET,
    highlight: dict[int, set[str]] | None = None,
    duplicates: dict[int, set[str]] | None = None,
    keep: dict[int, dict[str, str]] | None = None,
) -> Path:
    """Пишет книгу по шаблону заказчика. Шаблон не меняется.

    ``highlight`` — клетки, разошедшиеся с выгрузкой оператора: номер строки
    бланка (со второй) → буквы граф. Помечается клетка, а не строка:
    расхождение бывает в одном поле, и красить из-за него всю машину значит
    обесценить пометку.

    ``duplicates`` — строки повторных нажатий оператора. Цвет у них свой: это
    не спор о числе, а лишняя строка (решение 075). Клетка, попавшая в оба
    списка, красится как расхождение: утверждение о числе важнее отметки о
    лишней строке.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve() == template.resolve():
        raise ValueError("шаблон заказчика не перезаписывается: укажите другой файл")
    with zipfile.ZipFile(template) as src:
        part = _sheet_target(src, sheet)
        items = src.infolist()
        payload = {item.filename: src.read(item.filename) for item in items}
    marks: dict[int, dict[str, str]] = {}
    for index, columns in (duplicates or {}).items():
        marks.setdefault(index, {}).update({c: DUPLICATE_COLOR for c in columns})
    for index, columns in (highlight or {}).items():
        marks.setdefault(index, {}).update({c: MISMATCH_COLOR for c in columns})
    marked_style: dict[str, dict[int, int]] = {}
    if any(marks.values()):
        styles, marked_style = _with_fills(
            payload["xl/styles.xml"].decode("utf-8"),
            (MISMATCH_COLOR, DUPLICATE_COLOR),
        )
        payload["xl/styles.xml"] = styles.encode("utf-8")
    payload[part] = _filled_sheet(
        payload[part].decode("utf-8"), rows, marks, marked_style, keep,
    ).encode("utf-8")
    tmp = out.with_suffix(out.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in items:
            dst.writestr(item.filename, payload[item.filename])
    shutil.move(tmp, out)
    return out
