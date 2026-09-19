"""Цепочка по камере 1: кто за кем проехал и кого никто не записал.

К2 и К3 видят, что машина остановилась, но не знают, какая это машина:
бортового номера детектор не читает (решение 003). К1 стоит в ста метрах вверх
по ходу, снимает машины навстречу и читает борт, но стоянку от проезда не
отличает (решение 030). Строка рождается только из пары свидетельств.

Сопоставляются последовательности, а не отдельные моменты: заказчик назвал
порядок прибытия главным признаком («главное чтоб последовательность прибытия
была верной, это видно по бортовым номерам», решение 028). Поэтому здесь
выравнивание с монотонностью, а не поиск ближайшего соседа — ближайший сосед
на дистанции в 80 секунд между машинами это уже соседняя стоянка.
"""

from __future__ import annotations

from datetime import datetime, timedelta

import pytest

from paxcount.delivery.chain import MIN_PAIRS, align, fitted_shift

BASE = datetime(2026, 9, 10, 7, 0, 0)
MINUTE = timedelta(seconds=60)


def t(seconds: float) -> datetime:
    return BASE + timedelta(seconds=seconds)


def paired(rows: list[tuple[int | None, int | None]]) -> list[tuple[int, int]]:
    return [(i, j) for i, j in rows if i is not None and j is not None]


def test_pairs_never_cross():
    """Порядок не переставляется никогда — он и есть предмет проверки.

    Два проезда подряд и две стоянки подряд могут лечь по времени так, что
    второй проезд окажется ближе к первой стоянке. Скрестить их значит
    сказать заказчику, что машины пришли в другом порядке.
    """
    rows = align([t(0), t(30)], [t(25), t(55)], shift=timedelta(seconds=25),
                  tolerance=MINUTE)
    assert paired(rows) == [(0, 0), (1, 1)]
    left = [i for i, _ in rows if i is not None]
    right = [j for _, j in rows if j is not None]
    assert left == sorted(left) and right == sorted(right)


def test_a_gap_on_both_sides_stays_two_gaps():
    """Обе стороны пропустили по машине — получаются два пропуска, не пара.

    Ложная пара здесь хуже пропуска: она утверждает, что машина, которую
    видела только К1, и стоянка, которую видела только К2, — одно и то же ТС.
    """
    rows = align([t(0), t(120)], [t(300), t(420)],
                  shift=timedelta(0), tolerance=MINUTE)
    assert paired(rows) == []
    assert (0, None) in rows and (None, 0) in rows


def test_a_neighbour_beyond_tolerance_is_not_a_pair():
    """За допуском пары нет, как бы ни был сосед ближе прочих."""
    rows = align([t(0)], [t(200)], shift=timedelta(0), tolerance=MINUTE)
    assert paired(rows) == []


def test_the_shift_is_the_median_of_the_pairs():
    """Одно испорченное звено не утаскивает ленту за собой."""
    left = [t(0), t(100), t(200), t(300)]
    right = [t(-60), t(41), t(139), t(600)]
    rows = align(left, right, shift=timedelta(seconds=60), tolerance=MINUTE)
    assert fitted_shift(left, right, rows) == timedelta(seconds=60)


def test_a_file_without_enough_pairs_is_refused():
    """Сдвиг по двум парам — совпадение, а не замер.

    Сдвиг подгоняется по тем же звеньям, которые потом ищутся этим сдвигом.
    При двух парах подгонка объясняет сама себя, и лента уедет правдоподобно.
    """
    left = [t(0), t(100)]
    right = [t(60), t(160)]
    rows = align(left, right, shift=timedelta(seconds=60), tolerance=MINUTE)
    assert len(paired(rows)) < MIN_PAIRS
    with pytest.raises(ValueError, match="пар"):
        fitted_shift(left, right, rows, minimum=MIN_PAIRS)


def test_an_anchor_is_never_broken():
    """Пару, найденную по бортовому номеру, выравнивание не разрывает.

    Борт — прямое свидетельство (решение 068), время — косвенное: часы камер
    идут врозь, и по времени выгоднее оказывается другая раскладка. Там, где
    машина уже опознана, время не вправе её переставить.
    """
    left, right = [t(0), t(100)], [t(105), t(200)]
    free = align(left, right, shift=timedelta(0), tolerance=timedelta(seconds=120))
    assert paired(free) == [(0, 0), (1, 1)], "по одному времени лента ложится так"

    held = align(left, right, shift=timedelta(0), tolerance=timedelta(seconds=120),
                  anchors={1: 0})
    assert paired(held) == [(1, 0)]
    assert (0, None) in held
