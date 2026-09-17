"""Эталонный счёт по дверям — мера, которой проверяется всё остальное.

Два правила, каждое из которых уже стоило бы неверного вывода:

* **неизмеренное называется `N/A`, а не пустотой.** Пустая клетка в CSV
  неотличима от забытой: и «здесь считать было нечего», и «сюда не дописали»
  выглядят одинаково. `N/A` — это утверждение, и его видно;
* **`N/A` — не ноль.** «Через эту дверь никто не прошёл» и «эту дверь
  не считали» в таблице выглядят одинаково, а значат противоположное: первое
  — измерение, второе — его отсутствие. Считать второе нулём значит объявить
  совпадением то, что никто не проверял;
* **итог визита не хранится, а складывается.** Отдельная графа «вошло всего»
  рядом с числами по дверям — это копия, которая разъезжается с оригиналом
  ровно тогда, когда в разбивку внесут поправку.
"""

from __future__ import annotations

from pathlib import Path

from paxcount.truth_counts import DoorCount, load, save, totals


def rows() -> list[DoorCount]:
    return [
        DoorCount(visit="2", door=1, boarded=1, alighted=None),
        DoorCount(visit="2", door=2, boarded=2, alighted=None),
        DoorCount(visit="2", door=3, boarded=1, alighted=None),
        DoorCount(visit="6", door=1, boarded=None, alighted=None),
        DoorCount(visit="6", door=2, boarded=None, alighted=3),
    ]


def test_totals_sum_the_doors_of_one_visit():
    assert totals(rows())["2"] == (4, None), "вошло сложилось, выходов не считали"
    assert totals(rows())["6"] == (None, 3)


def test_unmeasured_door_is_not_a_zero():
    """У визита 6 первая дверь не в кадре: её пустота не должна стать нулём."""
    assert totals([DoorCount(visit="6", door=1, boarded=None, alighted=None)])["6"] == (
        None, None
    )


def test_round_trip_keeps_the_difference_between_unmeasured_and_zero(tmp_path: Path):
    path = tmp_path / "door_counts.csv"
    data = rows() + [DoorCount(visit="9", door=1, boarded=0, alighted=None)]
    save(path, data)
    back = load(path)
    assert back == data
    assert back[-1].boarded == 0 and back[-1].alighted is None


def test_unmeasured_is_written_as_na_not_as_an_empty_cell(tmp_path: Path):
    """Пустая клетка неотличима от забытой; `N/A` — заявление, а не пропуск."""
    path = tmp_path / "door_counts.csv"
    save(path, [DoorCount(visit="6", door=1, boarded=None, alighted=0)])
    assert path.read_text(encoding="utf-8").splitlines()[1] == "6,1,N/A,0"


def test_empty_cell_of_an_older_file_still_reads_as_unmeasured(tmp_path: Path):
    """Файлы, заполненные до этой конвенции, не должны стать нулями."""
    path = tmp_path / "door_counts.csv"
    path.write_text("visit,door,boarded,alighted\n6,1,,0\n", encoding="utf-8")
    assert load(path) == [DoorCount(visit="6", door=1, boarded=None, alighted=0)]
