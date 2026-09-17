"""Заполнение книги ПО ШАБЛОНУ заказчика, а не сборка своей с нуля.

Наш `write_rows` строит книгу сам и пишет всё встроенными строками. Для рабочей
выгрузки это годится, для файла, который уходит заказчику, — нет, и разница
видна в принятом им файле: там группа, дата, часы, минуты, маршрут, «вышло» и
«зашло» лежат ЧИСЛОВЫМИ ячейками. Текст «3» в графе «Вышло» Excel не суммирует:
сводная заказчика покажет ноль, и ошибка будет молчаливой.

Второе, что несёт шаблон, — выпадающие списки на «Тип ТС», «Размер ТС» и
«Наполненность», привязанные к служебному листу. Собрав книгу заново, мы их
теряем, и расшифровщик, открывший файл, останется без словарей заказчика.

Поэтому заполнение идёт В ШАБЛОН: подменяется только содержимое листа «Бланк»,
всё остальное в книге остаётся байт в байт.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path

from paxcount.delivery.fill import fill_template
from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize

SHEET = (
    '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
    '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
    '<dimension ref="A1:O171"/>'
    "<sheetData>"
    '<row r="1"><c r="A1" t="inlineStr"><is><t>Группа ОП</t></is></c></row>'
    "</sheetData>"
    '<dataValidations count="1"><dataValidation sqref="F1:F1048576"/></dataValidations>'
    "</worksheet>"
)


def template(path: Path) -> Path:
    with zipfile.ZipFile(path, "w") as z:
        z.writestr("xl/workbook.xml",
                    '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"'
                    ' xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
                    '<sheets><sheet name="Служебный лист" sheetId="1" r:id="rId1"/>'
                    '<sheet name="Бланк" sheetId="2" r:id="rId2"/></sheets></workbook>')
        z.writestr("xl/_rels/workbook.xml.rels",
                    '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                    '<Relationship Id="rId1" Target="worksheets/sheet1.xml"/>'
                    '<Relationship Id="rId2" Target="worksheets/sheet2.xml"/></Relationships>')
        z.writestr("xl/worksheets/sheet1.xml", "<worksheet><sheetData/></worksheet>")
        z.writestr("xl/worksheets/sheet2.xml", SHEET)
        z.writestr("xl/styles.xml",
                    '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
                    '<fills count="2"><fill><patternFill patternType="none"/></fill>'
                    '<fill><patternFill patternType="gray125"/></fill></fills>'
                    '<cellXfs count="6">'
                    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="1" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="14" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '<xf numFmtId="0" fontId="0" fillId="0" borderId="0" xfId="0"/>'
                    '</cellXfs></styleSheet>')
    return path


def row(**kw) -> DeliveryRow:
    base = dict(group="1317", date="10.09.2026", hours=7, minutes=5, stop="22739",
                 kind=VehicleKind.BUS, state_number="А000АА00", route="26",
                 size=VehicleSize.LARGE, alighted=3, boarded=2,
                 video="запись", operator="Иванов Иван")
    return DeliveryRow(**{**base, **kw})


def cells(path: Path) -> dict[str, tuple[str, str]]:
    """Ячейки второй строки листа «Бланк»: ссылка → (тип, значение)."""
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    body = re.findall(r'<row r="2".*?</row>', xml, re.S)[0]
    out = {}
    for ref, attrs, inner in re.findall(r'<c r="([A-Z]+)2"([^>]*)>(.*?)</c>', body, re.S):
        kind = (re.search(r't="(\w+)"', attrs) or [None, "число"])[1] if 't="' in attrs else "число"
        value = re.sub(r"<[^>]+>", "", inner)
        out[ref] = (kind, value)
    return out


def test_counts_are_written_as_numbers_not_text(tmp_path):
    """Текст «3» в графе «Вышло» заказчик не просуммирует, и не заметит этого."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    got = cells(book)
    assert got["K"] == ("число", "3"), "вышло"
    assert got["L"] == ("число", "2"), "зашло"
    assert got["C"] == ("число", "7") and got["D"] == ("число", "5"), "часы и минуты"
    assert got["H"] == ("число", "26"), "маршрут"


def test_the_date_is_a_real_date_not_a_string(tmp_path):
    """Дата — серийный номер под датовым стилем, как в принятом файле."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    kind, value = cells(book)["B"]
    assert kind == "число"
    assert value == "46275", "2026-09-10 в отсчёте Excel от 1899-12-30"


def test_not_counted_stays_na_and_does_not_become_zero(tmp_path):
    """N/A — это отказ считать по правилу инструкции, а не ноль пассажиров."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"),
                          [row(alighted=None, boarded=None)], tmp_path / "книга.xlsx")
    got = cells(book)
    assert got["K"][1] == "N/A" and got["L"][1] == "N/A"
    assert got["K"][0] != "число", "N/A не число"


def test_everything_the_template_carries_survives(tmp_path):
    """Выпадающие списки и служебный лист остаются: их даёт шаблон, не мы."""
    src = template(tmp_path / "шаблон.xlsx")
    book = fill_template(src, [row()], tmp_path / "книга.xlsx")
    with zipfile.ZipFile(book) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
        assert "dataValidations" in xml, "выпадающие списки шаблона"
        assert "Служебный лист" in z.read("xl/workbook.xml").decode("utf-8")
        assert z.read("xl/worksheets/sheet1.xml") == \
            zipfile.ZipFile(src).read("xl/worksheets/sheet1.xml")
    assert 'ref="A1:P2"' in xml, "размер листа пересчитан под число строк и граф"


# ---- Подсветка расхождений с оператором --------------------------------------
#
# Выгрузка оператора — второе независимое свидетельство. Где мы с ним расходимся,
# заказчик обязан это видеть в самой книге, а не в нашем письме: письмо теряется,
# книга остаётся. Помечается КЛЕТКА, а не строка — расхождение бывает в одном
# поле, и красить из-за него всю машину значит обесценить пометку.
#
# Шаблон заказчика заливок не содержит (в `styles.xml` только «none» и
# «gray125»), поэтому стиль приходится добавлять. Добавляется он копией
# нынешнего стиля графы плюс заливка: иначе помеченная клетка потеряет
# выравнивание и формат даты, и станет видно не расхождение, а нашу правку.

def style_of(path: Path, ref: str) -> int | None:
    with zipfile.ZipFile(path) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    m = re.search(rf'<c r="{ref}"([^>]*)>', xml)
    s = re.search(r's="(\d+)"', m.group(1)) if m else None
    return int(s.group(1)) if s else None


def fills_of(path: Path) -> list[str]:
    with zipfile.ZipFile(path) as z:
        st = z.read("xl/styles.xml").decode("utf-8")
    xfs = re.search(r'<cellXfs count="\d+">(.*?)</cellXfs>', st, re.S).group(1)
    return re.findall(r'<xf [^>]*?fillId="(\d+)"[^>]*/?>', xfs)


def test_a_disagreeing_cell_gets_its_own_style_with_a_fill(tmp_path):
    """Помечается клетка, и пометка видна в стиле, а не в тексте значения."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx", highlight={2: {"H"}})
    marked, plain = style_of(book, "H2"), style_of(book, "J2")
    assert marked is not None and marked != plain, "стиль помеченной графы другой"
    fills = fills_of(book)
    assert fills[marked] != "0", "у помеченного стиля есть заливка"
    assert fills[plain] == "0", "у соседней графы заливки нет"


def test_the_marked_cell_keeps_the_format_of_its_column(tmp_path):
    """Дата остаётся датой, даже когда расходится с оператором."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx", highlight={2: {"B"}})
    with zipfile.ZipFile(book) as z:
        st = z.read("xl/styles.xml").decode("utf-8")
    xfs = re.findall(r"<xf [^>]*/>", re.search(r'<cellXfs count="\d+">(.*?)</cellXfs>',
                                                  st, re.S).group(1))
    assert 'numFmtId="14"' in xfs[style_of(book, "B2")], "формат даты сохранён"


def test_without_disagreements_the_styles_are_left_alone(tmp_path):
    """Нечего помечать — книгу стилями не трогаем."""
    src = template(tmp_path / "шаблон.xlsx")
    book = fill_template(src, [row()], tmp_path / "книга.xlsx")
    with zipfile.ZipFile(book) as a, zipfile.ZipFile(src) as b:
        assert a.read("xl/styles.xml") == b.read("xl/styles.xml")


def test_an_empty_cell_is_still_marked_when_it_disagrees(tmp_path):
    """Пустая графа — самый интересный случай расхождения, и он терялся.

    Оператор записал маршрут, а мы его не опознали: в бланке графа пуста. Клетки
    без значения обычно не пишутся вовсе — и пометка вместе с ними исчезала,
    ровно там, где она нужнее всего.
    """
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row(route=None)],
                          tmp_path / "книга.xlsx", highlight={2: {"H"}})
    with zipfile.ZipFile(book) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert '<c r="H2"' in xml, "помеченная клетка пишется, даже пустая"
    assert fills_of(book)[style_of(book, "H2")] != "0"



# ---- Повторные нажатия оператора ---------------------------------------------
#
# Дубликаты остаются в книге (решение 075), и путать их с расхождением нельзя:
# «мы посчитали иначе» и «эту машину записали дважды» — разные сообщения.
# Проверяется не название цвета, а то, что заливки РАЗНЫЕ: оттенок дело вкуса,
# неразличимость — дефект.

def rgb_of(path: Path, ref: str) -> str | None:
    """Цвет заливки клетки: стиль → fillId → цвет."""
    with zipfile.ZipFile(path) as z:
        st = z.read("xl/styles.xml").decode("utf-8")
    fill_id = fills_of(path)[style_of(path, ref)]
    fills = re.findall(r"<fill>.*?</fill>",
                        re.search(r"<fills .*?</fills>", st, re.S).group(0), re.S)
    color = re.search(r'rgb="([0-9A-F]+)"', fills[int(fill_id)])
    return color.group(1) if color else None


def test_a_duplicate_row_is_painted_apart_from_a_disagreement(tmp_path):
    """Лишняя строка и спор о числе — разные вещи, и цвета разные."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row(), row()],
                          tmp_path / "книга.xlsx",
                          highlight={2: {"H"}}, duplicates={3: {"H"}})
    assert rgb_of(book, "H2") and rgb_of(book, "H3")
    assert rgb_of(book, "H2") != rgb_of(book, "H3")


def test_a_disagreement_outweighs_the_duplicate_colour(tmp_path):
    """Клетка в обоих списках красится как расхождение: оно говорит о числе."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row(), row()],
                          tmp_path / "книга.xlsx",
                          highlight={2: {"H"}}, duplicates={2: {"H"}, 3: {"H"}})
    assert rgb_of(book, "H2") != rgb_of(book, "H3")


# ---- Шестнадцатая графа: часы камеры, на которой видна посадка ----------------
#
# Шапку шаблона заполнение НЕ переписывает — она заказчика и остаётся его. Но
# нашей графы в его шапке нет вовсе, и без подписи столбец времён читается как
# мусор справа от таблицы. Значит подпись дописывается в ту же строку, а не
# вместо неё.


def test_the_extra_column_gets_its_own_header_beside_the_customers(tmp_path):
    from paxcount.delivery.xlsx import EXTRA_HEADERS

    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    with zipfile.ZipFile(book) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    head = re.findall(r'<row r="1".*?</row>', xml, re.S)[0]
    assert "Группа ОП" in head, "шапка заказчика на месте"
    assert EXTRA_HEADERS[0] in head, "наша графа подписана"


def test_the_camera_reading_is_written_as_text(tmp_path):
    """«К3 06:58:06» — не число и не время Excel: это адрес, куда перематывать."""
    from datetime import datetime

    book = fill_template(
        template(tmp_path / "шаблон.xlsx"),
        [row(camera="3", camera_ts=datetime(2026, 8, 10, 6, 58, 6))],
        tmp_path / "книга.xlsx")
    assert cells(book)["P"] == ("inlineStr", "К3 06:58:06")


def test_the_sheet_size_counts_our_column_too(tmp_path):
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    with zipfile.ZipFile(book) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert 'ref="A1:P2"' in xml
