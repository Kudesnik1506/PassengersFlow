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
    """Текст «3» заказчик не просуммирует, и не заметит этого.

    Графы счёта переехали: K и L — заказчика, он заполняет их рукой
    (решение 085), наш счёт стоит в Q и R. Требование к типу ячейки осталось
    прежним и переехало вместе со счётом: сводная складывает числа.
    """
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    got = cells(book)
    assert got["Q"] == ("число", "3"), "вышло, наш счёт"
    assert got["R"] == ("число", "2"), "зашло, наш счёт"
    assert got["C"] == ("число", "7") and got["D"] == ("число", "5"), "часы и минуты"
    assert got["H"] == ("число", "26"), "маршрут"


def test_the_date_is_a_real_date_not_a_string(tmp_path):
    """Дата — серийный номер под датовым стилем, как в принятом файле."""
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    kind, value = cells(book)["B"]
    assert kind == "число"
    assert value == "46275", "2026-09-10 в отсчёте Excel от 1899-12-30"


def test_the_customers_count_columns_are_left_untouched(tmp_path):
    """K и L сборка не заполняет ничем — ни числом, ни `N/A`.

    Прежде на их месте стоял наш счёт, а `None` превращался в `N/A`. И то и
    другое отменено: `N/A` по пункту 16 инструкции означает событие («ТС уже
    стояло с открытыми дверьми»), а не отсутствие счёта, и — главное — `N/A`
    не пустота, поэтому перенос ручного ввода его не спасал бы: вписанная
    заказчиком цифра затиралась бы каждой пересборкой.
    """
    book = fill_template(template(tmp_path / "шаблон.xlsx"),
                          [row(alighted=None, boarded=None)], tmp_path / "книга.xlsx")
    got = cells(book)
    assert got.get("K", ("", ""))[1] == ""
    assert got.get("L", ("", ""))[1] == ""
    assert cells(fill_template(template(tmp_path / "ш2.xlsx"), [row()],
                                tmp_path / "к2.xlsx")).get("K", ("", ""))[1] == "", \
        "даже когда счёт есть, он идёт в нашу графу, а не в графу заказчика"


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
    assert 'ref="A1:S2"' in xml, "размер листа пересчитан под число строк и граф"


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


def test_the_sheet_size_counts_our_columns_too(tmp_path):
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx")
    with zipfile.ZipFile(book) as z:
        xml = z.read("xl/worksheets/sheet2.xml").decode("utf-8")
    assert 'ref="A1:S2"' in xml


# ---- Ручной ввод заказчика переживает пересборку -------------------------------
#
# Книга лежит у заказчика и он в ней ПИШЕТ: наполненность по прибытию мы не
# считаем вовсе, и графу I он заполнял руками, глядя запись. Пересборка книги
# затирала эти клетки молча — потеря чужой работы без следа.
#
# Правило: чего наша сборка не пишет, того она и не стирает. Строки при этом
# сдвигаются (машина, пропущенная оператором, вписывается по времени), поэтому
# перенос идёт по ПРИМЕТАМ строки, а не по её номеру: номер после вставки
# указывает на соседнюю машину, и перенос молча положил бы данные не туда.


def test_a_cell_we_leave_empty_is_carried_over(tmp_path):
    from paxcount.delivery.manual import carried_over

    previous = [{"C": "6", "D": "56", "G": "А000АА00", "H": "26", "I": "Б"}]
    kept = carried_over(previous, [row(hours=6, minutes=56, route="26",
                                        state_number="А000АА00")])
    assert kept == {2: {"I": "Б"}}


def test_a_cell_we_fill_ourselves_is_not_carried_over(tmp_path):
    """Наше значение главнее: иначе правка данных никогда не доедет до книги."""
    from paxcount.delivery.manual import carried_over

    previous = [{"C": "6", "D": "56", "G": "А000АА00", "H": "64", "I": "Б"}]
    kept = carried_over(previous, [row(hours=6, minutes=56, route="26",
                                        state_number="А000АА00")])
    assert "H" not in kept.get(2, {}), "маршрут пишем мы"


def test_carrying_follows_the_row_that_moved(tmp_path):
    """Перед строкой вписали машину — ручная клетка едет за СВОЕЙ строкой."""
    from paxcount.delivery.manual import carried_over

    previous = [{"C": "6", "D": "56", "G": "А000АА00", "H": "26", "I": "Б"}]
    rows = [row(hours=6, minutes=50, route="50", state_number="А001АА00"),
             row(hours=6, minutes=56, route="26", state_number="А000АА00")]
    assert carried_over(previous, rows) == {3: {"I": "Б"}}


def test_a_row_that_vanished_carries_nothing(tmp_path):
    """Строки с такими приметами в новой книге нет — переносить некуда."""
    from paxcount.delivery.manual import carried_over

    previous = [{"C": "7", "D": "30", "G": "А777АА00", "H": "99", "I": "Б"}]
    assert carried_over(previous, [row(hours=6, minutes=56, route="26",
                                        state_number="А000АА00")]) == {}


def test_the_carried_value_reaches_the_sheet(tmp_path):
    book = fill_template(template(tmp_path / "шаблон.xlsx"), [row()],
                          tmp_path / "книга.xlsx", keep={2: {"I": "Б"}})
    assert cells(book)["I"] == ("inlineStr", "Б")


def test_the_carry_covers_every_column_not_just_occupancy():
    """Правило общее: пустая у нас графа не стирается, какой бы она ни была.

    Заказчик пишет в книге не только наполненность — и знать заранее, какие
    графы он тронет, мы не можем. Поэтому перенос не перечисляет графы, а
    смотрит, пусто ли у нас.

    Исключение одно и оно не про догадку: наши собственные графы (P, Q, R, S)
    не переносятся. В книге заказчика их нет вовсе, пока мы их не завели, и
    пустота в них — наше «сказать нечего», а не чужая работа.
    """
    from paxcount.delivery.fill import COLUMNS, EXTRA_COLUMNS
    from paxcount.delivery.manual import carried_over

    ours = row(hours=6, minutes=56, route="26", state_number="А000АА00",
                alighted=None, boarded=None, video="", operator="")
    empty = {c for c, v in zip(COLUMNS, __import__(
        "paxcount.delivery.xlsx", fromlist=["row_values"]).row_values(ours))
              if not (v or "").strip()}
    previous = [{"C": "6", "D": "56", "G": "А000АА00", "H": "26",
                  **{c: "чужое" for c in empty}}]
    kept = carried_over(previous, [ours])
    assert set(kept[2]) == empty - set(EXTRA_COLUMNS), "перенесено всё, что у нас пусто"
    assert len(empty) >= 5, f"граф, которые мы не заполняем, должно быть много: {empty}"


def test_rows_of_the_previous_book_that_found_no_place_are_named():
    """Строка прошлой книги не нашлась в новой — её ручной ввод переносить некуда.

    Молчать здесь нельзя: перенос по приметам не сработает, если у машины
    сменился номер (портал подставил госномер) или маршрут. Данные пропадут
    ровно так же, как при слепой перезаписи, только теперь незаметно ещё и для
    нас. Поэтому такие строки называются поимённо.
    """
    from paxcount.delivery.manual import orphaned

    previous = [{"C": "6", "D": "56", "G": "38099", "H": "26", "I": "Б"},
                 {"C": "7", "D": "02", "G": "А000АА00", "H": "50"}]
    rows = [row(hours=7, minutes=2, route="50", state_number="А000АА00")]
    lost = orphaned(previous, rows)
    assert len(lost) == 1, "вторая строка нашлась, первая нет"
    assert lost[0]["G"] == "38099" and lost[0]["I"] == "Б"


def test_a_previous_row_without_content_is_not_worth_naming():
    """Строка прошлой книги без данных сверх примет — не потеря, а шум в отчёте.

    Данными считается всё, что стоит вне примет: отличить ручной ввод от
    перенесённого у СГИНУВШЕЙ строки нечем, и осторожность тут дешевле потери.
    """
    from paxcount.delivery.manual import orphaned

    previous = [{"C": "6", "D": "56", "G": "38099"}]
    assert orphaned(previous, [row(hours=7, minutes=2)]) == []


def test_the_row_key_ignores_columns_the_customer_may_fill():
    """Приметы строятся только на том, что пишем МЫ.

    Заказчик вписал маршрут в строку, где мы его не опознали, — и строка
    перестала узнаваться по приметам, включавшим маршрут. То есть ровно тот
    ручной ввод, ради которого перенос и заведён, ломал сам перенос.
    """
    from paxcount.delivery.manual import carried_over

    previous = [{"C": "6", "D": "59", "G": "А000АА00", "H": "225", "I": "А"}]
    ours = row(hours=6, minutes=59, route=None, state_number="А000АА00")
    kept = carried_over(previous, [ours])
    assert kept == {2: {"H": "225", "I": "А"}}, "узналась, и оба значения на месте"


# ---- Открытую книгу не перезаписываем -----------------------------------------
#
# Сборка заменяет файл целиком, а Excel держит свою копию в памяти и подмены не
# видит. Дальше решает случай: сохранит заказчик — ляжет поверх пересборки,
# пересоберу я — ляжет поверх его правок. Оба исхода это потеря чужой работы,
# и ни один из них не виден в момент, когда происходит.


def test_an_open_workbook_is_recognised_by_the_lock_file(tmp_path):
    from paxcount.delivery.fill import opened_by

    book = tmp_path / "Книга.xlsx"
    book.write_bytes(b"")
    assert opened_by(book) is None, "замка нет — файл закрыт"
    lock = tmp_path / "~$Книга.xlsx"
    lock.write_bytes(b"")
    assert opened_by(book) == lock.name


def test_excel_truncating_the_lock_name_is_still_recognised(tmp_path):
    """Excel у длинных имён срезает начало: замок — ХВОСТ имени, а не имя.

    Промах здесь опаснее ложной тревоги: не увидев замка, сборка запишет в
    открытый файл, и потеря обнаружится через день.
    """
    from paxcount.delivery.fill import opened_by

    book = tmp_path / "Tablitsa_dlya_zapolnenia_2026.xlsx"
    book.write_bytes(b"")
    (tmp_path / "~$blitsa_dlya_zapolnenia_2026.xlsx").write_bytes(b"")
    assert opened_by(book) == "~$blitsa_dlya_zapolnenia_2026.xlsx"


def test_a_lock_of_another_workbook_is_not_ours(tmp_path):
    from paxcount.delivery.fill import opened_by

    book = tmp_path / "Книга.xlsx"
    book.write_bytes(b"")
    (tmp_path / "~$Другая.xlsx").write_bytes(b"")
    assert opened_by(book) is None


def test_the_libreoffice_lock_counts_too(tmp_path):
    from paxcount.delivery.fill import opened_by

    book = tmp_path / "Книга.xlsx"
    book.write_bytes(b"")
    (tmp_path / ".~lock.Книга.xlsx#").write_bytes(b"")
    assert opened_by(book) == ".~lock.Книга.xlsx#"


def test_a_customers_edit_is_written_over_our_value(tmp_path):
    """Правка заказчика ложится в клетку поверх нашего значения.

    Прежде `keep` спасал только пустую клетку — этого хватало, пока главным
    было наше число. Заказчик решил иначе: книгу ведёт он, и в клетке, которую
    он тронул, остаётся его значение, а наше уходит спором в графу S.
    """
    book = fill_template(template(tmp_path / "шаблон.xlsx"),
                          [row(route="26")], tmp_path / "книга.xlsx",
                          keep={2: {"H": "225"}})
    assert cells(book)["H"][1] == "225"
