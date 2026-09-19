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

# Наши графы — с шестнадцатой. В шапке заказчика их нет и быть не может.
# Подписи нарочно не похожи на подписи заказчика: графу нельзя принять за графу
# инструкции, и слово «наш» в каждой стоит именно за этим.
#
# P — часы камеры: время бланка стоит на ОБЩЕЙ шкале, а перематывать запись
#     проверяющий будет по часам конкретной камеры, и они расходятся на минуты.
# Q, R — наш счёт. Графы заказчика K и L сборка не трогает вовсе: он заполняет
#     их рукой (решение 085), а `N/A` по пункту 16 инструкции означает событие
#     («ТС уже стояло с открытыми дверьми»), а не отсутствие счёта.
# S — расхождения: всё, что мы заметили, но не вправе исправить молча
#     (решение 087).
EXTRA_HEADERS = [
    "Камера, на которой видна посадка-высадка, и время на её часах (наше)",
    "Вышло (наш счёт)",
    "Зашло (наш счёт)",
    "Расхождения и спорное (наше)",
]
SHEET_HEADERS = HEADERS + EXTRA_HEADERS

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
    """Строка модели в порядке колонок заказчика плюс наша шестнадцатая.

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
        # K и L — графы заказчика, и сборка их не заполняет ничем: ни числом,
        # ни `N/A`. Причина не в вежливости, а в переносе ручного ввода — он
        # спасает только пустую клетку, и `N/A` затирал бы вписанную цифру
        # каждой пересборкой (решение 085).
        "",
        "",
        _cell(row.comment_cell),
        row.video,
        row.operator,
        row.camera_cell,
        _cell(row.alighted),
        _cell(row.boarded),
        _cell(row.disagreement_cell),
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
    blank_rows = [SHEET_HEADERS] + [row_values(r) for r in rows]
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


def _sheet_part(z: zipfile.ZipFile, name: str | None) -> str:
    """Путь к листу книги по имени — через связи, а не по порядку.

    Порядок листов у заказчика плавает: «Бланк» второй в шаблоне и третий в
    принятой таблице, а выгрузка оператора вообще одностраничная и лист в ней
    зовётся `Sheet`. Опора на номер однажды прочитала бы словарь вместо данных.
    ``None`` означает «единственный лист» и допустим только когда он один.
    """
    wb = ET.fromstring(z.read("xl/workbook.xml"))
    rels = ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
    targets = {
        r.get("Id"): r.get("Target")
        for r in rels.iter("{http://schemas.openxmlformats.org/package/2006/relationships}"
                           "Relationship")
    }
    sheets = list(wb.iter(NS + "sheet"))
    if name is None:
        if len(sheets) != 1:
            raise ValueError(
                f"в книге {len(sheets)} листов — какой из них читать, надо назвать"
            )
        return "xl/" + targets[sheets[0].get(RNS + "id")].lstrip("/")
    for sheet in sheets:
        if (sheet.get("name") or "").strip() == name:
            return "xl/" + targets[sheet.get(RNS + "id")].lstrip("/")
    raise ValueError(f"в книге нет листа «{name}»")


def sheet_cells(path: Path, sheet: str | None = None) -> list[dict[str, str]]:
    """Строки листа как словари «буква колонки → значение».

    Единственный разбор xlsx в проекте: и книга заказчика, и выгрузка
    оператора читаются им же. Второй разбор того же формата разошёлся бы с
    первым на первой же особенности вроде общих строк или пустых ячеек.
    """
    with zipfile.ZipFile(path) as z:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = [
                "".join(t.text or "" for t in si.iter(NS + "t"))
                for si in ET.fromstring(z.read("xl/sharedStrings.xml")).iter(NS + "si")
            ]
        return _sheet_cells(z, _sheet_part(z, sheet), shared)


def _int_or_none(value: str) -> int | None:
    """Пусто или N/A — это «не считали», а не ноль (см. DeliveryRow.counted)."""
    text = (value or "").strip()
    if not text or text.upper() in {NA, "NA", "Н/Д"}:
        return None
    return int(float(text.replace(",", ".")))


def _split_comment(value: str) -> dict:
    """Разбирает графу M на код таблицы 2 и аномалии словами.

    Разделять обязательно: без этого следующая выгрузка получила бы в графу
    кода строку «7; разрыв записи...», и проверка по таблице 2 отвергла бы
    собственный файл проекта. Код — только если он идёт первым и является
    числом целиком: «7 машин» кодом не становится.
    """
    parts = [p.strip() for p in (value or "").split(";") if p.strip()]
    if not parts:
        return {"comment": None, "notes": ()}
    head, *rest = parts
    try:
        return {"comment": int(float(head.replace(",", "."))), "notes": tuple(rest)}
    except ValueError:
        return {"comment": None, "notes": tuple(parts)}


def _route_or_none(value: str) -> str | None:
    from .reconcile import UNKNOWN_ROUTE

    text = (value or "").strip()
    return None if not text or text == UNKNOWN_ROUTE else text


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


def _has_our_count_columns(cells: list[dict]) -> bool:
    """Знает ли книга наши графы счёта — по подписи в шапке, а не по наличию цифр.

    По цифрам судить нельзя: наша книга, собранная до первого счёта, пуста в Q
    и R точно так же, как чужая, и молчаливый откат к K и L выдал бы ручной
    ввод заказчика за наше измерение.
    """
    if not cells:
        return False
    header = cells[0]
    return (header.get("Q") or "").strip() == EXTRA_HEADERS[1]


def read_rows(path: Path) -> list[DeliveryRow]:
    """Читает лист «Бланк» книги заказчика в строки модели.

    Номер из графы раскладывается обратно в бортовой или государственный по
    виду ТС. Для автобуса исходный бортовой при этом уже утрачен — заменa у
    заказчика необратима (решение 026), и восстановить его из файла нечем.

    Счёт читается из наших граф Q и R, если книга их знает, и из граф заказчика
    K и L, если нет. Это не догадка, а признак самой книги: наши графы бывают
    только у нашей сборки, и там K и L — ручной ввод заказчика, принимать
    который за своё измерение нельзя. Книга без наших граф — принятый образец
    или чужая работа, и счёт в ней единственный, какой есть.
    """
    cells = sheet_cells(path, BLANK_SHEET)
    ours = _has_our_count_columns(cells)
    out_col, in_col = ("Q", "R") if ours else ("K", "L")

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
                # «N/A» в графе маршрута — это незнание по пункту 17, а не
                # маршрут с таким названием. Пересборка обязана прочитать его
                # так же, как написала, иначе сверка с оператором сравнит
                # подпись с числом.
                route=_route_or_none(cell.get("H", "")),
                occupancy=_enum_or_none(Occupancy, cell.get("I", "")),
                size=_enum_or_none(VehicleSize, cell.get("J", "")),
                alighted=_int_or_none(cell.get(out_col, "")),
                boarded=_int_or_none(cell.get(in_col, "")),
                **_split_comment(cell.get("M", "")),
                video=(cell.get("N") or "").strip(),
                operator=(cell.get("O") or "").strip(),
            )
        )
    return rows
