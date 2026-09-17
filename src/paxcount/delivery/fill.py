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
from .xlsx import BLANK_SHEET, NA, row_values

# Ноль отсчёта дат Excel. 1899-12-30, а не 1900-01-01: в отсчёте есть
# несуществующее 29 февраля 1900 года, и сдвиг на два дня — часть формата.
EXCEL_EPOCH = date(1899, 12, 30)

COLUMNS = "ABCDEFGHIJKLMNO"
# Стиль на графу — по принятому файлу заказчика.
STYLES = {"A": 3, "B": 4, "C": 1, "D": 1, "E": 3, "F": 3, "G": 1, "H": 1,
           "I": 1, "J": 1, "K": 1, "L": 1, "M": 5, "N": 3, "O": 3}
# Графы, которые заказчик держит числами. Графа M числовая, только пока в ней
# один код: с примечанием словами она становится текстом, это решает проверка
# ниже. Остальные — строки, даже если в них
# цифры: номер ОП «1715-15182» числом не является, и превращать его нельзя.
NUMERIC = set("ABCDHKLM")
DATE_COLUMN = "B"
# Заливка для клеток, разошедшихся с выгрузкой оператора. Шаблон заказчика
# заливок не содержит вовсе, поэтому стиль добавляется нами — копией нынешнего
# стиля графы плюс заливка. Копией, а не новым стилем: иначе помеченная клетка
# потеряет выравнивание и формат даты, и видно станет не расхождение, а правку.
MISMATCH_COLOR = "FFFFE699"


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


def _row_xml(index: int, values: list[str], marked: set[str],
              marked_style: dict[int, int]) -> str:
    cells = []
    for column, value in zip(COLUMNS, values):
        base = STYLES[column]
        style = marked_style.get(base, base) if column in marked else base
        cells.append(
            _cell(f"{column}{index}", column, value, style, column in marked))
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


def _with_fill(styles: str) -> tuple[str, dict[int, int]]:
    """Добавляет заливку и по копии каждого нужного стиля. Возвращает карту."""
    fills = re.search(r'<fills count="(\d+)">(.*?)</fills>', styles, re.S)
    fill_id = int(fills.group(1))
    styles = styles.replace(
        fills.group(0),
        f'<fills count="{fill_id + 1}">{fills.group(2)}'
        f'<fill><patternFill patternType="solid">'
        f'<fgColor rgb="{MISMATCH_COLOR}"/><bgColor indexed="64"/>'
        f"</patternFill></fill></fills>",
        1,
    )
    block = re.search(r'<cellXfs count="(\d+)">(.*?)</cellXfs>', styles, re.S)
    entries = re.findall(r"<xf [^>]*?/>|<xf [^>]*?>.*?</xf>", block.group(2), re.S)
    count = int(block.group(1))
    added, mapping = [], {}
    for base in sorted(set(STYLES.values())):
        clone = entries[base]
        # fillId ЗАМЕНЯЕТСЯ, а не дописывается: второй такой же атрибут Excel
        # прочитает по первому, и заливка молча не появится.
        if 'fillId="' in clone:
            clone = re.sub(r'fillId="\d+"', f'fillId="{fill_id}"', clone, count=1)
        else:
            clone = clone.replace("<xf ", f'<xf fillId="{fill_id}" ', 1)
        if 'applyFill="1"' not in clone:
            clone = clone.replace("<xf ", '<xf applyFill="1" ', 1)
        mapping[base] = count + len(added)
        added.append(clone)
    styles = styles.replace(
        block.group(0),
        f'<cellXfs count="{count + len(added)}">{block.group(2)}{"".join(added)}</cellXfs>',
        1,
    )
    return styles, mapping


def _filled_sheet(xml: str, rows: list[DeliveryRow],
                   highlight: dict[int, set[str]], marked_style: dict[int, int]) -> str:
    """Шапка шаблона остаётся своя, ниже ложатся наши строки."""
    header = re.search(r'<row r="1".*?</row>', xml, re.S)
    body = "".join(
        _row_xml(i, row_values(row), highlight.get(i, set()), marked_style)
        for i, row in enumerate(rows, start=2)
    )
    data = (header.group(0) if header else "") + body
    xml = re.sub(r"<sheetData\s*/>|<sheetData>.*?</sheetData>",
                  f"<sheetData>{data}</sheetData>", xml, count=1, flags=re.S)
    return re.sub(r'<dimension ref="A1:[A-Z]+\d+"\s*/>',
                   f'<dimension ref="A1:O{len(rows) + 1}"/>', xml, count=1)


def fill_template(
    template: Path,
    rows: list[DeliveryRow],
    out: Path,
    sheet: str = BLANK_SHEET,
    highlight: dict[int, set[str]] | None = None,
) -> Path:
    """Пишет книгу по шаблону заказчика. Шаблон не меняется.

    ``highlight`` — клетки, разошедшиеся с выгрузкой оператора: номер строки
    бланка (со второй) → буквы граф. Помечается клетка, а не строка:
    расхождение бывает в одном поле, и красить из-за него всю машину значит
    обесценить пометку.
    """
    out.parent.mkdir(parents=True, exist_ok=True)
    if out.resolve() == template.resolve():
        raise ValueError("шаблон заказчика не перезаписывается: укажите другой файл")
    with zipfile.ZipFile(template) as src:
        part = _sheet_target(src, sheet)
        items = src.infolist()
        payload = {item.filename: src.read(item.filename) for item in items}
    highlight = highlight or {}
    marked_style: dict[int, int] = {}
    if any(highlight.values()):
        styles, marked_style = _with_fill(payload["xl/styles.xml"].decode("utf-8"))
        payload["xl/styles.xml"] = styles.encode("utf-8")
    payload[part] = _filled_sheet(
        payload[part].decode("utf-8"), rows, highlight, marked_style,
    ).encode("utf-8")
    tmp = out.with_suffix(out.suffix + ".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as dst:
        for item in items:
            dst.writestr(item.filename, payload[item.filename])
    shutil.move(tmp, out)
    return out
