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


# --- Кандидаты: кого мы считаем пропущенной машиной -------------------------
#
# Ни один свидетель не надёжен в одиночку, и это замерено. К1 видит встречный
# поток: троллейбус на дальней полосе даёт такой же «ничей проезд», как машина
# у кармана. К2 фрагментирует стоянку: у контрольной машины (борт 38208) она
# зафиксирована двумя секундами вместо посадки. Поэтому кандидат — только
# пересечение: проезд рядом с камерой И стоянка, у которой нет своей строки.

from paxcount.delivery.chain import (  # noqa: E402
    MIN_CANDIDATE_PX, Passage, already_known, candidates,
)
from paxcount.delivery.visibility import Sighting  # noqa: E402


def passage(at: float, width: float = MIN_CANDIDATE_PX, track: int = 1) -> Passage:
    return Passage(camera="1", file="ф", track=track, start=t(at), end=t(at + 20),
                    peak=t(at + 5), box=(100.0, 200.0, 100.0 + width, 800.0),
                    frame_size=(1920, 1080))


def stop_at(at: float, seconds: float = 15.0) -> Sighting:
    return Sighting(start=t(at), end=t(at + seconds), box=(10.0, 10.0, 900.0, 700.0),
                     frame_size=(1920, 1080))


def test_a_passage_without_a_stop_is_not_a_candidate():
    """Проехал мимо — не наша машина.

    К1 стоит в ста метрах вверх по ходу и снимает весь поток, включая тех, кто
    остановку минует. Строка по такому проезду утверждала бы стоянку, которой
    не было, а ложная строка хуже отсутствующей.
    """
    assert candidates([passage(0)], [], shift=timedelta(0),
                       window=timedelta(seconds=60)) == []


def test_a_distant_passage_is_not_a_candidate():
    """Встречная полоса даёт такой же ничей проезд, как машина у кармана.

    Различает только размер в кадре: у подтверждённых оператором проездов
    ширина рамки от 900 px, у встречного троллейбуса — около 600.
    """
    far = passage(0, width=MIN_CANDIDATE_PX - 100)
    assert candidates([far], [stop_at(0)], shift=timedelta(0),
                       window=timedelta(seconds=60)) == []


def test_two_passages_of_one_stop_make_one_candidate():
    """Трек, разорванный надвое, — одна машина, а не две.

    Иначе в книгу уйдут две строки на одну стоянку — ровно то, за что заказчик
    бракует файл (решение 075).
    """
    found = candidates([passage(0, track=1), passage(12, track=2)], [stop_at(5)],
                        shift=timedelta(0), window=timedelta(seconds=60))
    assert len(found) == 1


def test_the_nearest_passage_wins_the_stop():
    """Стоянке достаётся тот проезд, что ближе по времени."""
    found = candidates([passage(0, track=1), passage(40, track=2)], [stop_at(45)],
                        shift=timedelta(0), window=timedelta(seconds=60))
    assert found[0].passage.track == 2


# --- Последняя проверка: не записан ли этот заезд уже ------------------------


def test_the_same_vehicle_at_the_same_minute_is_already_in_the_book():
    """Опознали машину — и нашли её же в книге рядом по времени.

    Выравнивание идёт по времени и ошибается: лишнее звено сдвигает разбор, и
    проезд остаётся «ничьим», хотя оператор машину записал. На боевом утре так
    вышло у четырёх кандидатов из двенадцати. Вторая строка на тот же заезд —
    ровно то, за что заказчик бракует файл (решение 075).
    """
    книга = [(t(0), "Р362НА198"), (t(300), "Х000ХХ178")]
    assert already_known(t(38), "Р362НА198", книга, window=timedelta(seconds=180))


def test_the_same_vehicle_on_its_next_round_is_a_new_row():
    """Машина ходит по кругу и возвращается — это другой заезд, а не дубль.

    В книге один госномер встречается до пяти раз за сутки. Отвергать строку
    по совпадению номера без времени значило бы терять все рейсы, кроме первого.
    """
    книга = [(t(0), "Р362НА198")]
    assert not already_known(t(3600), "Р362НА198", книга,
                              window=timedelta(seconds=180))


def test_another_vehicle_at_the_same_minute_does_not_block_the_row():
    """Соседняя машина в ту же минуту — обычное дело: они идут через 80 секунд."""
    книга = [(t(0), "Х000ХХ178")]
    assert not already_known(t(20), "Р362НА198", книга,
                              window=timedelta(seconds=180))


def test_without_a_number_nothing_can_be_told_apart():
    """Безымянную строку по номеру не проверить — и выдавать её за проверенную нельзя."""
    assert already_known(t(0), "", [(t(0), "")], window=timedelta(seconds=180)) is False
