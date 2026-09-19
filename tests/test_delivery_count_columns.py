"""Графы счёта: «Вышло» и «Зашло» заказчика — его, наш счёт стоит отдельно.

Владелец (18.09) заполняет K и L руками. До сих пор туда писал наш счёт, а
`None` превращался в `N/A` — и вот чем это плохо сразу дважды:

* **`N/A` не пустота.** Перенос ручного ввода (`fill.carried_over`) спасает
  только ту клетку, где у нас пусто. `N/A` затирал бы вписанную рукой цифру при
  каждой пересборке — ровно так были потеряны I2:I4;
* **`N/A` значит другое.** Пункт 16 инструкции: его ставят, когда ТС уже стояло
  на остановке с открытыми дверьми в начале или конце съёмки. В принятом
  заказчиком файле он стоит дважды на 312 строк, а у нас выходил бы на 289 из
  295. Подпись, означающая событие, не может стоять вместо молчания.

Наш счёт при этом не исчезает: он уходит в графы Q и R. Двумя графами, а не
одной строкой «12 / 8», потому что сводная заказчика суммирует числа, а текст
Excel не складывает.
"""

from __future__ import annotations

from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.xlsx import SHEET_HEADERS, row_values


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, board_number="1596", route="50",
                 size=VehicleSize.LARGE, video="запись", operator="Расшифровщик")
    return DeliveryRow(**{**base, **kw})


def cells(r: DeliveryRow) -> dict[str, str]:
    columns = "ABCDEFGHIJKLMNOPQRS"
    return dict(zip(columns, row_values(r)))


def test_the_build_leaves_the_customers_count_columns_alone():
    """Главное правило: K и L сборка не заполняет ничем, даже когда посчитала."""
    assert cells(row(alighted=12, boarded=8))["K"] == ""
    assert cells(row(alighted=12, boarded=8))["L"] == ""


def test_an_uncounted_row_does_not_get_na_either():
    """`N/A` по инструкции означает событие, а не отсутствие счёта."""
    assert cells(row())["K"] == ""
    assert cells(row())["L"] == ""


def test_our_count_lives_in_its_own_columns():
    got = cells(row(alighted=12, boarded=8))
    assert got["Q"] == "12", "вышло"
    assert got["R"] == "8", "зашло"


def test_our_count_columns_are_numbers_the_spreadsheet_can_add():
    """Текст «12» сводная заказчика не сложит — потому и две графы, а не одна."""
    got = cells(row(alighted=12, boarded=8))
    assert got["Q"].isdigit() and got["R"].isdigit()


def test_a_row_we_did_not_count_leaves_our_columns_empty_too():
    """Ноль — измерение, пусто — его отсутствие. Подменять одно другим нельзя."""
    got = cells(row())
    assert got["Q"] == "" and got["R"] == ""
    assert cells(row(alighted=0, boarded=0))["Q"] == "0"


def test_the_disagreement_column_is_the_last_one():
    got = cells(row(disagreements={"размер": "по таблице 3 такой пары не бывает"}))
    assert got["S"] == "размер: по таблице 3 такой пары не бывает"


def test_the_customers_fifteen_headers_do_not_move():
    """Пятнадцать граф заказчика стоят на своих местах, наши — правее."""
    assert SHEET_HEADERS[10] == "Вышло" and SHEET_HEADERS[11] == "Зашло"
    assert len(SHEET_HEADERS) == 19
    assert all("наш" in h or "наше" in h for h in SHEET_HEADERS[15:])


def test_a_hand_written_count_is_not_read_back_as_ours(tmp_path):
    """Цифра заказчика в K и L остаётся его: пересборка не выдаёт её за наш счёт.

    Иначе вышло бы худшее из возможного: его работа помечена жёлтым как наша,
    а наше измерение той строки потеряно.
    """
    from paxcount.delivery.xlsx import read_rows, write_rows

    from paxcount.delivery.xlsx import BLANK_SHEET, sheet_cells

    path = tmp_path / "книга.xlsx"
    write_rows(path, [row(alighted=None, boarded=None)])
    _put(path, {"K": "12", "L": "8"})

    written = sheet_cells(path, BLANK_SHEET)[1]
    assert (written.get("K"), written.get("L")) == ("12", "8"), \
        "иначе тест проходит вхолостую: в книгу ничего не вписалось"

    back = read_rows(path)[0]
    assert (back.alighted, back.boarded) == (None, None)


def _put(path, values: dict[str, str]) -> None:
    """Вписывает значения во вторую строку листа «Бланк», как это делает Excel."""
    import re
    import shutil
    import zipfile

    from paxcount.delivery.xlsx import BLANK_SHEET, _sheet_part

    with zipfile.ZipFile(path) as z:
        names = z.namelist()
        parts = {n: z.read(n) for n in names}
        target = _sheet_part(z, BLANK_SHEET)
    xml = parts[target].decode("utf-8")
    for column, value in values.items():
        cell = f'<c r="{column}2" t="inlineStr"><is><t>{value}</t></is></c>'
        xml = re.sub(rf'<c r="{column}2".*?(?:/>|</c>)', cell, xml, flags=re.S)
        if f'r="{column}2"' not in xml:
            head = xml.index('<row r="2"')
            close = xml.index("</row>", head)
            xml = xml[:close] + cell + xml[close:]
    parts[target] = xml.encode("utf-8")
    tmp = path.with_suffix(".tmp")
    with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as z:
        for name in names:
            z.writestr(name, parts[name])
    shutil.move(tmp, path)


def test_our_own_columns_are_not_carried_over_from_the_last_book():
    """Пустая клетка в НАШЕЙ графе — наше утверждение, а не пробел.

    Перенос ручного ввода бережёт то, чего сборка не пишет. Но графы P, Q, R и
    S существуют только потому, что мы их завели, и молчание в них значит
    «сказать нечего». Первая боевая сборка это и показала: устаревшие
    расхождения вернулись из прошлой книги в 292 строки из 295 и остались бы
    там навсегда.
    """
    from paxcount.delivery.manual import carried_over

    ours = row(hours=7, minutes=2, board_number="1596")
    was = [{"C": "7", "D": "2", "G": "1596",
            "I": "А",                       # ручной ввод заказчика — беречь
            "S": "маршрут: портал говорит 64",   # наше прошлое слово — не беречь
            "Q": "12"}]                     # наш прошлый счёт — не беречь
    kept = carried_over(was, [ours])
    assert kept == {2: {"I": "А"}}
