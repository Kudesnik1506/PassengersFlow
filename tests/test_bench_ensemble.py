"""Ансамбль: согласие методов — вес кандидата, число дверей — арбитр.

Простое объединение кандидатов не работает: у каждого метода своя шкала
уверенности, и отбор «по score» превращается в отбор «по методу, который
печатает числа покрупнее». Поэтому вес кандидата здесь — не его score, а
количество РАЗНЫХ методов, назвавших это место. Это сравнимо между методами по
построению и не зависит от их внутренних шкал.

Дальше работает арбитр из решения 038: из согласованных мест берётся ровно
столько, сколько у машины дверей по таблице 3.
"""

from __future__ import annotations

from paxcount.bench.methods.ensemble import consensus_candidates

BODY = (300.0, 400.0, 1500.0, 800.0)  # ширина 1200 px


def door(x0: float) -> tuple[float, float, float, float]:
    return (x0, 500.0, x0 + 80.0, 780.0)


def test_agreement_of_two_methods_outweighs_a_lone_find():
    found = consensus_candidates({
        "a": [door(400.0), door(1000.0)],
        "b": [door(404.0)],
    }, BODY)
    by_x = sorted(found, key=lambda c: c.x0)
    assert by_x[0].score == 2.0, "место, названное обоими методами"
    assert by_x[1].score == 1.0


def test_close_finds_become_one_candidate():
    """Разброс в несколько пикселей — это одна дверь, а не две."""
    found = consensus_candidates({"a": [door(400.0)], "b": [door(406.0)]}, BODY)
    assert len(found) == 1


def test_one_method_naming_a_place_twice_does_not_inflate_the_weight():
    """Вес — число методов, а не число прямоугольников: иначе шумный победит."""
    found = consensus_candidates({"a": [door(400.0), door(404.0)]}, BODY)
    assert len(found) == 1
    assert found[0].score == 1.0


def test_candidates_come_back_in_body_fractions():
    """Формат общий с doorprop: арбитр принимает доли рамки, не пиксели."""
    found = consensus_candidates({"a": [door(600.0)]}, BODY)
    assert 0.0 <= found[0].x0 <= 1.0 and 0.0 <= found[0].x1 <= 1.0


def test_no_candidates_yield_nothing_rather_than_a_phantom():
    assert consensus_candidates({"a": [], "b": []}, BODY) == []
