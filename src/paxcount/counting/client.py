"""Счёт пакета моделью: политика прогонов, арбитр, учёт расхода.

Конфигурация взята из решения 024, а не из общих соображений. Там замерено:
Haiku на видео 05 дважды независимо назвал вышедших вошедшими — это переворот
направления, которого в готовом файле не видно; Sonnet на тех же роликах
совпал с эталоном или ошибся на одного. Отсюда «2×Sonnet, арбитр Opus на
расхождении» и запрет на Haiku как дешёвый уровень.

Политика отделена от транспорта намеренно. Клиент, который проверяется только
настоящим вызовом, проверяется редко — а каждый такой вызов стоит денег
заказчика. Поэтому `send` здесь обычная функция: в бою её строит
`anthropic_sender`, в тестах подставляется заглушка.

Расход пишется на каждый ответ в `logs/token_spend.jsonl`. Без него вопрос
«модель дороже классического счётчика или нет» решается на глаз, а именно ради
этого сравнения модель и строится (решение 020).
"""

from __future__ import annotations

import json
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..settings import LOG_DIR
from .packages import Package
from .parse import AnswerFormatError, CountAnswer, Consensus, consensus, parse_answer

# Рабочая модель счёта и арбитр — решение 024. Haiku здесь нет и не должно
# появиться без нового замера: дело не в цене ошибки, а в её направлении.
COUNT_MODEL = "claude-sonnet-5"
ARBITER_MODEL = "claude-opus-5"
RUNS = 2

SPEND_PATH = LOG_DIR / "token_spend.jsonl"
MAX_TOKENS = 1024


@dataclass(frozen=True)
class Reply:
    """Один ответ модели с расходом — то, что отдаёт транспорт."""

    text: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0


Sender = Callable[[str, Sequence[bytes], str], Reply]


@dataclass(frozen=True)
class CountResult:
    """Итог счёта одного пакета: согласие, все ответы, расход."""

    package: Package
    consensus: Consensus
    replies: tuple[Reply, ...]
    answers: tuple[CountAnswer, ...]
    unparsable: int

    @property
    def input_tokens(self) -> int:
        return sum(r.input_tokens for r in self.replies)

    @property
    def output_tokens(self) -> int:
        return sum(r.output_tokens for r in self.replies)


def count_package(
    package: Package,
    prompt: str,
    send: Sender,
    *,
    runs: int = RUNS,
    model: str = COUNT_MODEL,
    arbiter: str = ARBITER_MODEL,
    spend_path: Path | None = None,
) -> CountResult:
    """Считает один пакет: `runs` прогонов, при расхождении — один арбитр.

    Пустой пакет не отправляется вовсе: платить за кадры, которых нет, незачем,
    а ноль отсюда был бы выдумкой — отсутствие кадров означает «не считали».
    """
    if not package.frames:
        return CountResult(
            package=package,
            consensus=Consensus(None, None, False, "кадров в пакете нет — счёта нет", ()),
            replies=(), answers=(), unparsable=0,
        )

    images = [f.jpeg for f in package.frames]
    replies: list[Reply] = []
    answers: list[CountAnswer] = []
    unparsable = 0

    for _ in range(runs):
        reply = send(prompt, images, model)
        replies.append(reply)
        parsed = _parse(reply)
        if parsed is None:
            unparsable += 1
        else:
            answers.append(parsed)

    verdict = consensus(answers)
    if not verdict.agreed:
        reply = send(prompt, images, arbiter)
        replies.append(reply)
        parsed = _parse(reply)
        if parsed is None:
            unparsable += 1
        else:
            answers.append(parsed)
        verdict = consensus(answers)

    _record_spend(package, replies, spend_path)
    return CountResult(
        package=package, consensus=verdict, replies=tuple(replies),
        answers=tuple(answers), unparsable=unparsable,
    )


def _parse(reply: Reply) -> CountAnswer | None:
    """Неразобранный ответ — это не ноль, а отсутствие ответа."""
    try:
        return parse_answer(reply.text)
    except AnswerFormatError:
        return None


def _record_spend(package: Package, replies: list[Reply], path: Path | None) -> None:
    target = path or SPEND_PATH
    if not replies:
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).isoformat()
    with target.open("a", encoding="utf-8") as f:
        for reply in replies:
            f.write(json.dumps({
                "ts": stamp,
                "visit_key": package.visit_key,
                "camera": package.camera,
                "door": package.door,
                "model": reply.model,
                "frames": len(package.frames),
                "input_tokens": reply.input_tokens,
                "output_tokens": reply.output_tokens,
            }, ensure_ascii=False) + "\n")


def anthropic_sender(api_key: str | None = None, max_tokens: int = MAX_TOKENS) -> Sender:
    """Строит боевой транспорт поверх SDK Anthropic.

    SDK импортируется внутри: тестовая группа проекта его не ставит, а политика
    выше обязана проверяться и без него. Отсутствие ключа — внятная ошибка на
    старте, а не пустой ответ в середине смены.
    """
    import base64

    import anthropic

    client = anthropic.Anthropic(api_key=api_key) if api_key else anthropic.Anthropic()

    def send(prompt: str, images: Sequence[bytes], model: str) -> Reply:
        content: list[dict] = [
            {
                "type": "image",
                "source": {
                    "type": "base64",
                    "media_type": "image/jpeg",
                    "data": base64.b64encode(jpeg).decode("ascii"),
                },
            }
            for jpeg in images
        ]
        content.append({"type": "text", "text": prompt})
        message = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            messages=[{"role": "user", "content": content}],
        )
        text = "".join(block.text for block in message.content if block.type == "text")
        return Reply(
            text=text, model=model,
            input_tokens=message.usage.input_tokens,
            output_tokens=message.usage.output_tokens,
        )

    return send
