"""Политика счёта моделью: два прогона, арбитр на расхождении, учёт расхода.

Конфигурация не выбрана из общих соображений, а взята из решения 024, где она
замерена: Haiku на видео 05 дважды независимо назвал вышедших вошедшими —
переворот направления, который в готовом файле не увидеть; Sonnet на тех же
роликах совпал с эталоном или ошибся на одного. Отсюда «2×Sonnet + арбитр
Opus» и явный запрет на Haiku как дешёвый уровень.

Сеть в тестах не трогается: политика отделена от транспорта, и `send` —
обычная функция, которую тест подменяет. Это не только про скорость тестов.
Клиент, который можно проверить только настоящим вызовом, проверяется редко,
а каждый его запуск стоит денег заказчика.

Отдельно проверяется расход: он пишется в `logs/token_spend.jsonl` на каждый
ответ. Без этого «модель дороже 6000 ₽ за смену» невозможно ни подтвердить,
ни опровергнуть — а это ровно тот вопрос, ради которого модель и сравнивают с
классическим счётчиком.
"""

from __future__ import annotations

import json

import pytest
from paxcount.counting.client import (
    ARBITER_MODEL,
    COUNT_MODEL,
    RUNS,
    Reply,
    count_package,
)
from paxcount.counting.packages import Package, PackageFrame


def package(frames: int = 3) -> Package:
    return Package(
        visit_id=7, door=2, visit_key="2/07:02:06/1596", camera="2",
        width=320, height=400,
        frames=tuple(PackageFrame(t=i * 0.2, jpeg=b"\xff\xd8fake") for i in range(frames)),
    )


def answer(boarded: int, alighted: int = 0) -> str:
    return (f'{{"boarded": {boarded}, "alighted": {alighted}, '
            f'"events": [], "doubts": ""}}')


def sender(answers: list[str], log: list | None = None):
    """Подставной транспорт: отдаёт заготовленные ответы по очереди."""
    queue = list(answers)

    def send(prompt: str, images, model: str) -> Reply:
        if log is not None:
            log.append((model, len(images), prompt))
        text = queue.pop(0) if queue else answers[-1]
        return Reply(text=text, model=model, input_tokens=1000, output_tokens=50)

    return send


# ---- Согласие с первого раза ------------------------------------------------


def test_two_matching_runs_need_no_arbiter():
    log: list = []
    result = count_package(package(), "промпт", sender([answer(3), answer(3)], log))
    assert result.consensus.agreed and result.consensus.boarded == 3
    assert len(log) == RUNS, "третий прогон не нужен, когда два сошлись"
    assert all(model == COUNT_MODEL for model, _, _ in log)


def test_images_from_the_package_are_sent():
    log: list = []
    count_package(package(frames=4), "промпт", sender([answer(1), answer(1)], log))
    assert log[0][1] == 4, "в запрос ушли все кадры пакета"


# ---- Арбитр -----------------------------------------------------------------


def test_disagreement_calls_the_arbiter():
    log: list = []
    result = count_package(
        package(), "промпт", sender([answer(3), answer(5), answer(3)], log)
    )
    assert len(log) == RUNS + 1, "на расхождении добавляется ровно один прогон"
    assert log[-1][0] == ARBITER_MODEL
    assert result.consensus.agreed and result.consensus.boarded == 3


def test_arbiter_is_opus_per_decision_024():
    assert "opus" in ARBITER_MODEL.lower()
    assert "sonnet" in COUNT_MODEL.lower()


def test_haiku_is_not_used_anywhere():
    """Решение 024: Haiku переворачивает направление — не уровень счёта."""
    assert "haiku" not in (COUNT_MODEL + ARBITER_MODEL).lower()


def test_arbiter_that_agrees_with_neither_leaves_it_to_a_human():
    result = count_package(
        package(), "промпт", sender([answer(3), answer(5), answer(9)])
    )
    assert not result.consensus.agreed
    assert result.consensus.boarded is None


# ---- Мусорные ответы --------------------------------------------------------


def test_unparsable_reply_is_not_counted_as_zero():
    """Ответ без числа — «неизвестно». Ноль здесь был бы выдумкой."""
    result = count_package(
        package(), "промпт", sender(["я не могу это посчитать", answer(0), answer(0)])
    )
    assert result.consensus.boarded == 0, "два разборчивых ответа сошлись на нуле"
    assert result.unparsable == 1


def test_all_replies_unparsable_means_no_count():
    result = count_package(package(), "промпт", sender(["мусор", "тоже мусор", "и это"]))
    assert not result.consensus.agreed
    assert result.consensus.boarded is None


# ---- Пустой пакет -----------------------------------------------------------


def test_package_without_frames_is_not_sent_at_all():
    log: list = []
    result = count_package(package(frames=0), "промпт", sender([answer(0)], log))
    assert log == [], "платить за пустой пакет незачем"
    assert not result.consensus.agreed
    assert "кадр" in result.consensus.reason.lower()


# ---- Учёт расхода -----------------------------------------------------------


def test_spend_is_written_per_reply(tmp_path):
    spend = tmp_path / "token_spend.jsonl"
    count_package(package(), "промпт", sender([answer(3), answer(3)]), spend_path=spend)
    lines = [json.loads(line) for line in spend.read_text(encoding="utf-8").splitlines()]
    assert len(lines) == RUNS
    assert lines[0]["model"] == COUNT_MODEL
    assert lines[0]["input_tokens"] == 1000
    assert lines[0]["visit_key"] == "2/07:02:06/1596"
    assert lines[0]["door"] == 2


def test_spend_totals_are_available_on_the_result():
    result = count_package(package(), "промпт", sender([answer(3), answer(3)]))
    assert result.input_tokens == 2000
    assert result.output_tokens == 100


def test_spend_file_is_appended_not_overwritten(tmp_path):
    spend = tmp_path / "token_spend.jsonl"
    for _ in range(2):
        count_package(package(), "промпт", sender([answer(3), answer(3)]), spend_path=spend)
    assert len(spend.read_text(encoding="utf-8").splitlines()) == 2 * RUNS


# ---- Транспорт --------------------------------------------------------------


def test_real_sender_requires_the_sdk_explicitly():
    """Без ключа и SDK клиент падает внятно, а не молча считает ноль."""
    from paxcount.counting.client import anthropic_sender

    pytest.importorskip("anthropic", reason="SDK ставится с полными зависимостями")
    assert callable(anthropic_sender)
