"""Промпт счёта: правила заказчика и запреты, которые нельзя забыть.

Промпт — это текст, и проверить в нём можно только присутствие правил. Но
именно присутствие здесь и теряется: правила счёта пришли из инструкции
заказчика уже после того, как мы прогнали первые слепые счёты, и те прогоны
считали не по ним. Тихо разойтись с заказчиком в том, кого считать, — дороже
любой ошибки округления: ребёнок на руках, посчитанный как пассажир, делает
строку неверной, а платят за файл целиком.

Отдельно проверяются два запрета. Лица не описывать и номера не читать — это
не вежливость, а решения 018 и 019: в счётную модель кадры уходить могут, но
приметы людей из них наружу не возвращаются. И честный отказ вместо догадки:
сегодняшний замер по номерам показал, что отказ «не читается» — самый полезный
вид ошибки, потому что его видно.
"""

from __future__ import annotations

import pytest
from paxcount.counting.prompt import COUNTING_RULES, build_count_prompt
from paxcount.windows import Window


def window(**over) -> Window:
    base = dict(visit_id=3, t0=100.0, t1=130.0, box=(0.0, 0.0, 800.0, 600.0),
                person_px=120.0, rivals=(), contested=False)
    base.update(over)
    return Window(**base)


# ---- Правила счёта из инструкции -------------------------------------------


@pytest.mark.parametrize("rule", [
    "на руках",          # детей на руках не считаем
    "коляск",            # и тем более в коляске
    "самостоятельно",    # детей, идущих самостоятельно, — считаем
    "контрол",           # контролёров считаем
])
def test_instruction_rules_present(rule):
    assert rule in COUNTING_RULES.lower()


def test_one_person_counted_once():
    assert "один раз" in COUNTING_RULES.lower()


def test_passers_by_are_not_counted():
    """Идущие мимо — главный источник ложного счёта на остановке."""
    assert "мимо" in COUNTING_RULES.lower()


# ---- Запреты ---------------------------------------------------------------


def test_prompt_forbids_describing_faces():
    assert "лиц" in build_count_prompt(window()).lower()


def test_prompt_forbids_reading_plates():
    assert "номер" in build_count_prompt(window()).lower()


def test_prompt_asks_for_honest_refusal():
    """Отказ должен быть разрешён явно, иначе модель догадывается молча."""
    text = build_count_prompt(window()).lower()
    assert "не видно" in text or "не уверен" in text


# ---- Соседние машины -------------------------------------------------------


def test_rivals_produce_an_explicit_warning():
    """46 % машин стоят рядом с другой: кого считать, надо сказать прямо."""
    text = build_count_prompt(window(rivals=(4,)))
    assert "обведён" in text.lower() or "обведен" in text.lower()
    assert "сосед" in text.lower() or "другой" in text.lower()


def test_no_rivals_no_confusing_warning():
    assert "соседн" not in build_count_prompt(window()).lower()


def test_contested_window_says_the_count_is_doubtful():
    text = build_count_prompt(window(rivals=(4,), contested=True))
    assert "перекрыва" in text.lower() or "закрыва" in text.lower()


# ---- Форма ответа ----------------------------------------------------------


def test_prompt_demands_json_with_both_numbers():
    text = build_count_prompt(window())
    assert "json" in text.lower()
    assert "boarded" in text and "alighted" in text


def test_prompt_states_the_time_window():
    text = build_count_prompt(window(t0=100.0, t1=130.0))
    assert "30" in text, "длина окна в секундах названа"


def test_prompt_mentions_frame_naming():
    """Кадры именуются секундой — модель должна знать, что время в имени."""
    assert "секунд" in build_count_prompt(window()).lower()
