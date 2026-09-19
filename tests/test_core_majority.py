"""Строгое большинство — одно на весь проект.

Правило жило в двух копиях: `counting/parse.py` считал согласием «двое и
больше», `delivery/edgedoors.py` — больше половины. На трёх прогонах копии
совпадают, на четырёх расходятся: 2:2 первая назовёт согласием, вторая —
спором. Двое против двоих это спор, и разрешать его порядком в `Counter`
значит бросать жребий и выдавать жребий за согласие.
"""

from __future__ import annotations

from paxcount.core.majority import strict


def test_a_tie_is_not_a_majority():
    """Ровно половина — не большинство, как бы ни легли значения в счётчике."""
    value, why = strict([2, 2, 3, 3], "дверей всего")
    assert value is None
    assert "разошлись" in why and "2, 2, 3, 3" in why


def test_more_than_half_wins():
    value, why = strict([7, 7, 8], "дверей всего")
    assert (value, why) == (7, "")


def test_a_single_run_agrees_with_itself_and_that_is_the_callers_problem():
    """Один прогон большинством является — сколько их нужно, решает зовущий.

    Порог «меньше двух не согласие» (решение 024) живёт у вызывающего:
    `edgedoors.agreed` и `counting.parse.consensus` отвечают на него по-разному
    и обязаны отвечать сами, иначе правило размажется по двум местам.
    """
    assert strict([5], "дверей всего") == (5, "")


def test_nothing_to_count_is_not_an_answer():
    value, why = strict([], "дверей всего")
    assert value is None and why
