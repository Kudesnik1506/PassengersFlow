"""Промпт опознания: что за машина приехала.

Это второй промпт проекта, и правила у него обратны первому. Счётному
(`counting/prompt.py`) читать номера запрещено: ему нужны люди, а номер в его
ответе — лишние персональные данные. Опознавательному читать номера велено
заказчиком: «Госномера пишем обязательно», это колонка G таблицы.
Противоречия здесь нет — запрет 018 про то, что уходит наружу, а наружу и в
том, и в другом случае уходят только те поля, которых ждёт бланк.

Наполненность прочтением кадра не занимаемся (решение 032): колонку I
заполняет только выгрузка оператора, где она есть, а не наша модель. Размер —
из ответа заказчика от 2026-09-15: средний 2 двери, большой 3, особо большой 4
с гармошкой; у трамвая вагоны считаются по гармошкам.

Отказ проверяется отдельным тестом, потому что он — самое ценное свойство.
Замер по госномеру показал, чем кончается догадка: с кадра прочиталось
`Р750НЕ178`, портал знает `Р750НЕ198` — основа верна, регион выдуман. Такую
ошибку в файле заказчика не видно.
"""

from __future__ import annotations

import pytest

from paxcount.counting.identify import build_identify_prompt


def prompt() -> str:
    return build_identify_prompt()


# ---- Что нужно прочитать ---------------------------------------------------


@pytest.mark.parametrize("field", ["бортов", "государствен", "маршрут"])
def test_prompt_asks_for_every_column_it_fills(field):
    assert field in prompt().lower()


def test_answer_is_json_with_named_fields():
    text = prompt()
    assert "json" in text.lower()
    for key in ("board_number", "state_number", "route", "size", "kind"):
        assert key in text


# ---- Наполненность не читаем (решение 032) ---------------------------------


def test_prompt_does_not_ask_for_occupancy():
    """Наполненность считаем не мы: колонка I идёт от выгрузки оператора."""
    text = prompt().lower()
    assert "occupancy" not in text
    assert "наполнен" not in text


# ---- Размер по дверям ------------------------------------------------------


@pytest.mark.parametrize("word", ["двер", "гармошк", "вагон"])
def test_size_is_defined_by_doors_and_joints(word):
    assert word in prompt().lower()


# ---- Отказ вместо догадки --------------------------------------------------


def test_refusal_is_allowed_explicitly():
    """Раньше эту фразу непреднамеренно давала шкала наполненности («Не видно
    свободных сидячих мест») — с её удалением (решение 032) отказ должен
    остаться явным сам по себе, в тексте про чтение номеров."""
    text = prompt().lower()
    assert "не видно" in text or "не читается" in text or "не различим" in text


def test_region_must_not_be_guessed():
    """178 против 198 — ошибка, которой в готовом файле не увидеть."""
    assert "регион" in prompt().lower()


def test_no_personal_details_in_the_answer():
    assert "лиц" in prompt().lower()


# --- Камера 1: размер с неё не читается ------------------------------------


def test_size_is_not_asked_of_camera_one():
    """Размер определяется числом дверей, а дверей с К1 не видно.

    Двери у российских машин справа; К1 стоит навстречу потоку и снимает морду
    и левый борт (решение 030). Ответ о дверях с этой камеры был бы выдумкой, а
    выдумка в графе J неотличима от чтения.
    """
    text = build_identify_prompt(ask_size=False)
    assert "двер" not in text.lower()
    assert "size" not in text
    assert "board_number" in text, "борт с К1 читается — ради него всё и затевается"


def test_fields_without_a_strict_majority_stay_empty():
    """Каждое поле судится отдельно: борт прочли все, маршрут — нет."""
    from paxcount.counting.identify import Identity, agreed_identity

    answers = [
        Identity(kind="Автобус", board_number="38208", route="226"),
        Identity(kind="Автобус", board_number="38208", route="26"),
        Identity(kind="Автобус", board_number="38208", route=None),
    ]
    agreed, why = agreed_identity(answers)
    assert (agreed.board_number, agreed.kind) == ("38208", "Автобус")
    assert agreed.route is None and "маршрут" in why["route"]


def test_a_single_run_is_not_an_agreement():
    """Один прогон — мнение, а не согласие (решение 024)."""
    from paxcount.counting.identify import Identity, agreed_identity

    agreed, why = agreed_identity([Identity(board_number="38208")])
    assert agreed.board_number is None
    assert "один прогон" in why.get("всё", "")


def test_an_answer_is_read_out_of_the_noise_around_it():
    """Модель любит обрамить JSON словами — это не повод терять ответ."""
    from paxcount.counting.identify import parse_identity

    got = parse_identity('Вот что вижу:\n{"kind": "Автобус", "board_number": "1596",'
                          ' "state_number": null, "route": "50", "doubts": ""}\nГотово.')
    assert (got.kind, got.board_number, got.route) == ("Автобус", "1596", "50")
    assert got.state_number is None
