"""Чтение и запись книги заказчика — своими силами, без новой зависимости.

Почему не openpyxl: тесты в CI идут на группе `test`, куда сознательно не
входят зависимости проекта (17 пакетов, установка за секунды). Ради одной
записи книги эта группа выросла бы, а дубль границ версий между
`[project.dependencies]` и группой пришлось бы поддерживать вручную. Формат
xlsx — это zip с несколькими XML, и мы уже читаем его так же при разборе
документов заказчика.

Строки пишутся встроенными (`t="inlineStr"`), без таблицы общих строк: книга
получается длиннее, но writer — короче и без состояния, а размер здесь никого
не заботит (312 строк).
"""

from __future__ import annotations

import zipfile
from pathlib import Path
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

from .model import DeliveryRow, Occupancy, VehicleKind, VehicleSize

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
RNS = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"

# Лист с данными. Имя, а не номер: в шаблоне он второй, в принятой таблице —
# третий, и опора на порядок однажды прочитает словарь вместо данных.
BLANK_SHEET = "Бланк"
DICT_SHEET = "Служебный лист"
COMMENT_SHEET = "Комментарии для ЦТП"

# Шапка — дословно из шаблона заказчика, включая «Наполенность» (так у них) и
# хвост «. Скопировать сюда». Если на той стороне книгу разбирают по тексту
# заголовка, любая наша правка превратит колонку в неизвестную.
HEADERS = [
    "Группа ОП",
    "Дата (например, 01.01.2026)",
    "Время (Часы)",
    "Время (Минуты)",
    "Номер ОП",
    "Тип ТС",
    "Бортовой или государственный номер ТС",
    "Номер маршрута",
    "Наполенность по прибытию",
    "Размер ТС",
    "Вышло",
    "Зашло",
    "Комментарий",
    "Название видеофайла. Скопировать сюда",
    "Фамилия, имя расшифровщика",
]

# Отказ от счёта по правилу инструкции: ТС уже стояло с открытыми дверями в
# начале или конце съёмки. Не ноль — см. `DeliveryRow.counted`.
NA = "N/A"

COMMENT_TABLE = [
    ("Ситуация", "Комментарий"),
    ("На камеру не попала первая дверь транспорта, но есть возможность посчитать людей.", "1"),
    ("На камеру не попало первые две двери (если транспорт трёхдверный) или первая половина "
     "автобуса или трамвая (если четырехдверный и более), но людей посчитать можно.", "2"),
    ("На камеру не попала последняя дверь, но можно посчитать людей.", "3"),
    ("На камеру не попало последние две двери (если транспорт трехдверный) или вторая половина "
     "автобуса или трамвая (если четырехдверный и более), но посчитать людей можно.", "4"),
    ("На камеру не попала первая дверь и невозможно посчитать людей, вошедших и вышедших из неё.",
     "5"),
    ("На камеру не попали первые две двери (если транспорт трёхдверный) или первая половина "
     "автобуса или трамвая (если четырехдверный и более), но невозможно посчитать людей, "
     "вошедших и вышедших из них.", "6"),
    ("На камеру не попала последняя дверь и невозможно посчитать людей, вошедших и вышедших "
     "из неё.", "7"),
    ("На камеру не попало последние две двери (если транспорт трехдверный) или вторая половина "
     "автобуса или трамвая (если четырехдверный и более) и посчитать людей нельзя.", "8"),
]


def _col(i: int) -> str:
    """Номер колонки в буквенное имя: 0 -> A, 25 -> Z, 26 -> AA."""
    name = ""
    i += 1
    while i:
        i, rem = divmod(i - 1, 26)
        name = chr(65 + rem) + name
    return name


def _sheet_xml(rows: list[list[str]]) -> bytes:
    out = [
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>',
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">',
        "<sheetData>",
    ]
    for r, values in enumerate(rows, start=1):
        out.append(f'<row r="{r}">')
        for c, value in enumerate(values):
            if value is None or value == "":
                continue
            ref = f"{_col(c)}{r}"
            out.append(
                f'<c r="{ref}" t="inlineStr"><is><t xml:space="preserve">'
                f"{escape(str(value))}</t></is></c>"
            )
        out.append("</row>")
    out.append("</sheetData></worksheet>")
    return "".join(out).encode("utf-8")


def _cell(value: object) -> str:
    return "" if value is None else str(value)


def row_values(row: DeliveryRow) -> list[str]:
    """Строка модели в порядке колонок заказчика.

    Бортовой номер сюда не попадает намеренно: в графе заказчика у автобуса
    стоит государственный, а бортовой остаётся у нас для перепроверки.
    """
    return [
        row.group,
        row.date,
        _cell(row.hours),
        _cell(row.minutes),
        row.stop,
        row.kind.value,
        _cell(row.number),
        _cell(row.route),
        row.occupancy.value if row.occupancy else "",
        row.size.value if row.size else "",
        _cell(row.alighted) if row.alighted is not None else NA,
        _cell(row.boarded) if row.boarded is not None else NA,
        _cell(row.comment),
        row.video,
        row.operator,
    ]


def write_rows(path: Path, rows: list[DeliveryRow], blank_last: bool = False) -> Path:
    """Записывает книгу заказчика: словари, бланк и таблица комментариев.

    `blank_last` переставляет листы так, как они лежат в принятой заказчиком
    таблице. Порядок для чтения значения не имеет (лист ищется по имени), и
    существует ради проверки, что это действительно так.
    """
    dict_rows = [["Тип ТС", "", "Размер ТС", "", "Наполненность"]]
    kinds, sizes, occ = list(VehicleKind), list(VehicleSize), list(Occupancy)
    for i in range(max(len(kinds), len(sizes), len(occ))):
        dict_rows.append([
            kinds[i].value if i < len(kinds) else "", "",
            sizes[i].value if i < len(sizes) else "", "",
            occ[i].value if i < len(occ) else "",
        ])
    blank_rows = [HEADERS] + [row_values(r) for r in rows]
    comment_rows = [list(pair) for pair in COMMENT_TABLE]

    sheets = [(DICT_SHEET, dict_rows), (BLANK_SHEET, blank_rows),
              (COMMENT_SHEET, comment_rows)]
    if blank_last:
        sheets = [(COMMENT_SHEET, comment_rows), (DICT_SHEET, dict_rows),
                  (BLANK_SHEET, blank_rows)]

    path.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(path, "w", zipfile.ZIP_DEFLATED) as z:
        overrides = "".join(
            f'<Override PartName="/xl/worksheets/sheet{i}.xml" ContentType='
            '"application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        z.writestr("[Content_Types].xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                   '<Default Extension="rels" ContentType='
                   '"application/vnd.openxmlformats-package.relationships+xml"/>'
                   '<Default Extension="xml" ContentType="application/xml"/>'
                   '<Override PartName="/xl/workbook.xml" ContentType='
                   '"application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
                   f"{overrides}</Types>")
        z.writestr("_rels/.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns='
                   '"http://schemas.openxmlformats.org/package/2006/relationships">'
                   '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/'
                   'officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
                   "</Relationships>")
        names = "".join(
            f'<sheet name="{escape(name)}" sheetId="{i}" r:id="rId{i}"/>'
            for i, (name, _) in enumerate(sheets, start=1)
        )
        z.writestr("xl/workbook.xml",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
                   'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                   f"<sheets>{names}</sheets></workbook>")
        rels = "".join(
            f'<Relationship Id="rId{i}" Type="http://schemas.openxmlformats.org/officeDocument/'
            f'2006/relationships/worksheet" Target="worksheets/sheet{i}.xml"/>'
            for i in range(1, len(sheets) + 1)
        )
        z.writestr("xl/_rels/workbook.xml.rels",
                   '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                   '<Relationships xmlns='
                   f'"http://schemas.openxmlformats.org/package/2006/relationships">{rels}'
                   "</Relationships>")
        for i, (_, data) in enumerate(sheets, start=1):
            z.writestr(f"xl/worksheets/sheet{i}.xml", _sheet_xml(data))
    return path


def _sheet_cells(z: zipfile.ZipFile, part: str, shared: list[str]) -> list[dict[str, str]]:
    root = ET.fromstring(z.read(part))
    rows = []
    for row in root.iter(NS + "row"):
        cells: dict[str, str] = {}
        for c in row.iter(NS + "c"):
            ref = "".join(ch for ch in (c.get("r") or "") if ch.isalpha())
            kind = c.get("t")
            if kind == "inlineStr":
                node = c.find(f"{NS}is/{NS}t")
                value = node.text if node is not None else ""
            else:
                node = c.find(NS + "v")
                if node is None:
                    continue
                value = shared[int(node.text)] if kind == "s" else node.text
            if value is not None:
                cells[ref] = value
        if cells:
            rows.append(cells)
    return rows


def _find_blank(z: zipfile.ZipFile) -> str:
    """Путь к листу «Бланк» — по имени через связи книги, не по порядку."""
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {
        r.get("Id"): r.get("Target")
        for r in rels.iter("{http://schemas.openxmlformats.org/package/2006/relationships}"
                           "Relationship")
    }
    for sheet in wb.iter(NS + "sheet"):
        if (sheet.get("name") or "").strip() == BLANK_SHEET:
            target = targets[sheet.get(RNS + "id")]
            return "xl/" + target.lstrip("/")
    raise ValueError(f"в книге нет листа «{BLANK_SHEET}»")


def _int_or_none(value: str) -> int | None:
    """Пусто или N/A — это «не считали», а не ноль (см. DeliveryRow.counted)."""
    text = (value or "").strip()
    if not text or text.upper() in {NA, "NA", "Н/Д"}:
        return None
    return int(float(text.replace(",", ".")))


def _enum_or_none(enum_cls, value: str):
    text = (value or "").strip()
    if not text:
        return None
    # У заказчика в принятой таблице встречается «Особо большой » с хвостовым
    # пробелом. Это то же значение, и терять из-за него строку нельзя.
    for member in enum_cls:
        if member.value.strip().casefold() == text.casefold():
            return member
    raise ValueError(f"{enum_cls.__name__}: значение {text!r} вне словаря заказчика")


def read_rows(path: Path) -> list[DeliveryRow]:
    """Читает лист «Бланк» книги заказчика в строки модели.

    Номер из графы раскладывается обратно в бортовой или государственный по
    виду ТС. Для автобуса исходный бортовой при этом уже утрачен — заменa у
    заказчика необратима (решение 026), и восстановить его из файла нечем.
    """
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [
                "".join(t.text or "" for t in si.iter(NS + "t"))
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si")
            ]
        cells = _sheet_cells(z, _find_blank(z), shared)

    rows: list[DeliveryRow] = []
    for cell in cells[1:]:  # первая строка — шапка
        kind = _enum_or_none(VehicleKind, cell.get("F", ""))
        if kind is None:
            continue  # строка без вида ТС — пустой хвост бланка
        number = (cell.get("G") or "").strip() or None
        from .model import KINDS_NEEDING_STATE_NUMBER

        state = number if kind in KINDS_NEEDING_STATE_NUMBER else None
        board = None if kind in KINDS_NEEDING_STATE_NUMBER else number
        rows.append(
            DeliveryRow(
                group=(cell.get("A") or "").strip(),
                date=(cell.get("B") or "").strip(),
                hours=int(float(cell.get("C") or 0)),
                minutes=int(float(cell.get("D") or 0)),
                stop=(cell.get("E") or "").strip(),
                kind=kind,
                board_number=board,
                state_number=state,
                route=(cell.get("H") or "").strip() or None,
                occupancy=_enum_or_none(Occupancy, cell.get("I", "")),
                size=_enum_or_none(VehicleSize, cell.get("J", "")),
                alighted=_int_or_none(cell.get("K", "")),
                boarded=_int_or_none(cell.get("L", "")),
                comment=_int_or_none(cell.get("M", "")),
                video=(cell.get("N") or "").strip(),
                operator=(cell.get("O") or "").strip(),
            )
        )
    return rows
