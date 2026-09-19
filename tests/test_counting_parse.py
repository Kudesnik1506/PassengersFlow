"""Разбор ответа счётной модели и согласие двух прогонов.

Две вещи, за которые здесь платят дорого.

**Мусор не должен превращаться в ноль.** Ответ, из которого не вынулось число,
и ответ «событий не было» выглядят в таблице одинаково — нулём. Первый при
этом означает «мы не знаем», второй — «мы посмотрели и там пусто». Слить их
значит получить нули, которых никто не наблюдал, и не отличить поломку клиента
от пустого заезда. Поэтому непонятный ответ — исключение, а не ноль.

**Расхождение не усредняется.** Если один прогон увидел 3 входа, а другой 5,
то 4 — это число, которого не видел никто. Разошлись — значит либо решает
третий прогон большинством, либо строка уходит человеку. Так же устроена и
ручная методика: диапазоны запрещены, подгонка под ожидаемое — главный
источник ошибок этой сессии.
"""

from __future__ import annotations

import pytest
from paxcount.counting.parse import (
    AnswerFormatError,
    CountAnswer,
    consensus,
    parse_answer,
)

GOOD = """{"boarded": 3, "alighted": 1,
 "events": [{"t": 4.5, "direction": "in"}, {"t": 6.0, "direction": "out"}],
 "doubts": ""}"""


# ---- Разбор ----------------------------------------------------------------


def test_plain_json_is_parsed():
    answer = parse_answer(GOOD)
    assert (answer.boarded, answer.alighted) == (3, 1)
    assert len(answer.events) == 2
    assert answer.events[0].direction == "in"
    assert answer.events[0].t == pytest.approx(4.5)


def test_json_in_markdown_fence_is_parsed():
    """Модель часто оборачивает ответ в ```json — это не повод падать."""
    answer = parse_answer(f"```json\n{GOOD}\n```")
    assert (answer.boarded, answer.alighted) == (3, 1)


def test_json_with_chatter_around_is_parsed():
    answer = parse_answer(f"Вот результат подсчёта:\n{GOOD}\nГотово.")
    assert answer.boarded == 3


def test_zero_counts_are_a_valid_answer():
    """Пустой заезд — законный исход, а не признак поломки."""
    answer = parse_answer('{"boarded": 0, "alighted": 0, "events": [], "doubts": ""}')
    assert (answer.boarded, answer.alighted) == (0, 0)


def test_doubts_are_preserved():
    text = '{"boarded": 1, "alighted": 0, "events": [], "doubts": "дверь закрыта кузовом"}'
    assert "кузов" in parse_answer(text).doubts


def test_raw_text_is_kept_for_audit():
    assert parse_answer(GOOD).raw == GOOD


@pytest.mark.parametrize("junk", [
    "",
    "Извините, я не могу посчитать людей на этих кадрах.",
    "{не json вовсе",
    '{"events": [], "doubts": ""}',           # нет обязательных чисел
    '{"boarded": "три", "alighted": 1, "events": [], "doubts": ""}',
])
def test_unparsable_answer_raises_instead_of_returning_zero(junk):
    """Ответ без числа — это «неизвестно», и оно не равно нулю."""
    with pytest.raises(AnswerFormatError):
        parse_answer(junk)


def test_negative_count_is_rejected():
    with pytest.raises(AnswerFormatError):
        parse_answer('{"boarded": -1, "alighted": 0, "events": [], "doubts": ""}')


def test_unknown_direction_is_rejected():
    with pytest.raises(AnswerFormatError):
        parse_answer('{"boarded": 1, "alighted": 0, '
                      '"events": [{"t": 1.0, "direction": "вбок"}], "doubts": ""}')


# ---- Согласие прогонов ------------------------------------------------------


def answer(boarded: int, alighted: int) -> CountAnswer:
    return parse_answer(
        f'{{"boarded": {boarded}, "alighted": {alighted}, "events": [], "doubts": ""}}'
    )


def test_two_matching_runs_agree():
    result = consensus([answer(3, 1), answer(3, 1)])
    assert result.agreed
    assert (result.boarded, result.alighted) == (3, 1)


def test_two_diverging_runs_do_not_agree():
    """Разошлись по входу — вход спорный, но согласие по выходу не теряется."""
    result = consensus([answer(3, 1), answer(5, 1)])
    assert not result.agreed
    assert result.boarded is None, "по входу согласия нет"
    assert result.alighted == 1, "по выходу оба прогона сказали одно и то же"
    assert "вошло" in result.reason


def test_disagreement_is_never_averaged():
    """4 — число, которого не видел ни один прогон. Среднего здесь быть не может."""
    result = consensus([answer(3, 0), answer(5, 0)])
    assert result.boarded != 4


def test_third_run_breaks_the_tie_by_majority():
    """Арбитр по решению 024: на расхождении запускается третий прогон."""
    result = consensus([answer(3, 1), answer(5, 1), answer(3, 1)])
    assert result.agreed and result.boarded == 3


def test_three_different_answers_need_a_human():
    result = consensus([answer(3, 0), answer(4, 0), answer(5, 0)])
    assert not result.agreed
    assert "человек" in result.reason.lower()


def test_directions_are_judged_separately():
    """Совпали по входу, разошлись по выходу — принимаем вход, выход спорный."""
    result = consensus([answer(3, 1), answer(3, 2), answer(3, 9)])
    assert result.boarded == 3, "по входу согласие есть"
    assert result.alighted is None, "по выходу большинства нет"
    assert not result.agreed


def test_single_answer_is_not_a_consensus():
    """Один прогон — это мнение, а не согласие: решение 024 требует двух."""
    result = consensus([answer(3, 1)])
    assert not result.agreed
    assert "один" in result.reason.lower() or "двух" in result.reason.lower()


def test_empty_input_is_not_a_zero_result():
    result = consensus([])
    assert not result.agreed and result.boarded is None


def test_an_even_split_is_not_an_agreement():
    """Двое на двоих — спор, а не согласие.

    Правило «двое и больше» верно на трёх прогонах и врёт на четырёх: при
    2:2 `Counter` вернёт того, кто попался первым, и жребий поедет в книгу
    как согласие. Большинство здесь строгое — больше половины.
    """
    result = consensus([answer(3, 0), answer(3, 0), answer(5, 0), answer(5, 0)])
    assert not result.agreed
    assert result.boarded is None
