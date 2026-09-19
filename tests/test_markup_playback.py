"""Проигрывание на экране разметки: чем платится каждый показанный кадр.

Экран читает кадры по одному запросу на кадр. Пока по записи ходят кнопками
«+1 кадр» и «+1 с», цена запроса не видна: раз в секунду её не замечаешь. На
проигрывании она становится потолком скорости, и замер это показывает:

* открыть файл, перемотать, прочитать — 65 мс, то есть не быстрее 15 кадров/с;
* перемотка в уже открытом файле — 29 мс;
* чтение следующего кадра подряд — около 1 мс (885 кадров/с).

Разница между вторым и третьим и решает, получится ли догнать x30: при показе
15 кадров/с и тридцатикратной скорости с диска надо снимать по 60 кадров на
каждый показанный, и снимать их подряд.

Отсюда правило: если нужный кадр впереди и недалеко, его дешевле дочитать, а не
перематывать. «Недалеко» — не вкусовое: дальше некоторого расстояния перемотка
к ближайшему опорному кадру обгоняет последовательное чтение.
"""

from __future__ import annotations

from paxcount.webui.markup import SEQUENTIAL_REACH, reads_forward


def test_the_next_frame_is_read_and_not_sought():
    """Следующий кадр подряд — самый частый случай проигрывания."""
    assert reads_forward(position=1000, frame=1001) is True


def test_a_frame_within_reach_is_read_forward():
    """Шаг проигрывания — десятки кадров, и это всё ещё дешевле перемотки."""
    assert reads_forward(position=1000, frame=1000 + SEQUENTIAL_REACH) is True


def test_a_distant_frame_is_sought():
    """Дальний прыжок дочитыванием обошёлся бы в тысячи лишних кадров."""
    assert reads_forward(position=1000, frame=1000 + SEQUENTIAL_REACH + 1) is False


def test_a_frame_behind_the_cursor_is_sought():
    """Назад последовательным чтением не ходят: файл читается только вперёд."""
    assert reads_forward(position=1000, frame=999) is False


def test_the_frame_under_the_cursor_is_read_at_once():
    """Кадр, на котором стоит курсор, достаётся одним чтением без перемотки."""
    assert reads_forward(position=1000, frame=1000) is True


def test_a_frame_already_read_is_sought_again():
    """Прочитанный кадр остался позади курсора — за ним только перемотка."""
    assert reads_forward(position=1001, frame=1000) is False


def test_a_closed_file_is_always_sought():
    """Курсора нет — сравнивать не с чем, и читать неоткуда."""
    assert reads_forward(position=None, frame=0) is False
