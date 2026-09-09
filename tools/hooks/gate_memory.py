#!/usr/bin/env python3
"""Гейт памяти решений: проверяет записи в docs/decisions и сверяет каталог.

Зачем кодом, а не на слово: память протухает тише, чем код. Неверная или
потерянная запись не роняет прогон — она молча уводит следующую сессию не туда.
Всё, что можно проверить детерминированно, проверяется здесь (принцип 7).

Формат заголовка — JSON в блоке ``+++``. Не YAML: шаг проверок в CI работает без
установки зависимостей проекта, PyYAML там нет. И не TOML: ``tomllib`` появился
только в Python 3.11, а хуки исполняются системным ``python3`` — на машине
разработчика это 3.9.6. JSON разбирается любой версией и уже служит форматом всех
данных проекта (разметка зон, baseline), то есть второго формата не заводится.

Устройство повторяет tools/hooks/gate_zones.py, а тесты зовут функции отсюда, а
не пересказывают правила (принцип 5).
"""

from __future__ import annotations

import re
import subprocess
import sys
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DECISIONS_DIR = ROOT / "docs" / "decisions"

# Закрытые словари. Открытый словарь ломает поиск молча: в разных сессиях
# появятся «двери», «дверная-зона» и «door-zone», и grep по тегу перестанет
# находить половину записей, ничего не сообщив.
VOCABULARIES: dict[str, frozenset[str]] = {
    # Жанр. «ограничение» — граница метода, которую следующий читатель почти
    # наверняка примет за недоделку и полезет чинить; для памяти между
    # сессиями это самый опасный класс.
    "kind": frozenset({"решение", "тупик", "ограничение"}),
    # Кто вправе отменить. Признак операциональный, а не тематический:
    # «бизнес или техника» на реальном материале не различается, а «кто вправе
    # отменить» — всегда.
    "authority": frozenset({"заказчик", "замер", "уклад"}),
    "status": frozenset({"действует", "отменено", "отложено"}),
    "origin": frozenset({"задним числом", "по ходу"}),
}

TAGS = frozenset({
    "дверные-зоны", "трекинг", "визиты", "метрика",
    "набор-данных", "кэш", "гейты", "публикация", "персональные-данные",
})

# Условия пересмотра. По ним ищут grep'ом, когда меняется обстановка: получив
# первую боевую запись, достаточно одной команды, чтобы узнать, что перечитать.
INVALIDATIONS = frozenset({
    "появились боевые записи",
    "сменился детектор",
    "эталон вырос",
    "сменилась камера или ракурс",
    "решение заказчика",
    "сменился формат кэша",
    "узкое место сместилось на детекцию",
})

REQUIRED = ("title", "date", "kind", "authority", "status", "tags",
            "verify", "invalidates_on", "evidence", "origin")

# Грамматика поля verify. Пустого значения нет: «нечем проверить» тоже ответ,
# но его надо назвать явно, иначе поле молча вырождается.
VERIFY_PREFIXES = ("test:", "gate:", "metric:", "none:")
EVIDENCE_PREFIXES = ("commit:", "file:", "cmd:", "measurement:")

MAX_LINES = 60
NAME_RE = re.compile(r"^\d{3}-[a-z0-9-]+\.md$")


def split_front(text: str) -> tuple[str, str]:
    """Отделяет JSON-заголовок от тела. Пустой заголовок — тоже ответ."""
    if not text.startswith("+++"):
        return "", text
    end = text.find("\n+++", 3)
    if end == -1:
        return "", text
    return text[3:end], text[end + 4:]


def check_record(path: Path) -> list[str]:
    """Замечания по одной записи. Пустой список — записи в порядке."""
    problems: list[str] = []
    name = path.name

    if not NAME_RE.match(name):
        problems.append(
            f"{name}: имя должно быть NNN-slug.md только из ASCII. Кириллица в "
            "именах на macOS пишется как NFD, а Linux в CI читает NFC — файл "
            "есть локально и отсутствует на сервере"
        )

    text = path.read_text(encoding="utf-8")
    raw, body = split_front(text)
    if not raw.strip():
        return problems + [f"{name}: нет заголовка +++ … +++"]

    try:
        meta = json.loads(raw)
    except json.JSONDecodeError as exc:
        return problems + [f"{name}: заголовок не разбирается как JSON — {exc}"]

    for field in REQUIRED:
        if field not in meta:
            problems.append(f"{name}: нет поля {field}")
    if any(f"нет поля" in p for p in problems):
        return problems

    for field, allowed in VOCABULARIES.items():
        if meta[field] not in allowed:
            problems.append(
                f"{name}: {field} = {meta[field]!r}, допустимы {sorted(allowed)}"
            )

    for tag in meta["tags"]:
        if tag not in TAGS:
            problems.append(f"{name}: неизвестный тег {tag!r}, допустимы {sorted(TAGS)}")
    for cond in meta["invalidates_on"]:
        if cond not in INVALIDATIONS:
            problems.append(
                f"{name}: неизвестное условие пересмотра {cond!r}, "
                f"допустимы {sorted(INVALIDATIONS)}"
            )

    if not str(meta["verify"]).startswith(VERIFY_PREFIXES):
        problems.append(
            f"{name}: verify должно начинаться с одного из {list(VERIFY_PREFIXES)}"
        )
    for item in meta["evidence"]:
        if not str(item).startswith(EVIDENCE_PREFIXES):
            problems.append(
                f"{name}: evidence {item!r} — нужен префикс из {list(EVIDENCE_PREFIXES)}"
            )

    # «Отложено» без условия возврата читается как «никогда», а это неправда:
    # отложенное ждёт наступления условия, и оно обязано быть названо.
    if meta["status"] == "отложено" and not meta["invalidates_on"]:
        problems.append(
            f"{name}: статус «отложено» требует непустого invalidates_on — "
            "иначе запись читается как «никогда»"
        )

    # Стрелка одна: README ссылается на записи, записи на README — никогда.
    # Заголовки README меняются, якорь протухает молча.
    if "README.md#" in body:
        problems.append(
            f"{name}: ссылка на якорь README. Заголовки меняются, якорь протухнет "
            "молча — ссылаться должен README на запись, не наоборот"
        )

    if len(text.splitlines()) > MAX_LINES:
        problems.append(
            f"{name}: {len(text.splitlines())} строк, длиннее {MAX_LINES}. "
            "Память читается пачкой, длинная запись вытесняет соседние"
        )

    problems += _check_links(path, meta)
    return problems


def repo_is_shallow() -> bool:
    """Клон на одну ревизию — обычное состояние в CI.

    ``actions/checkout`` по умолчанию выгружает только HEAD, и ссылки на прежние
    коммиты в такой копии не разрешаются. Сообщать при этом «коммита нет в
    истории» — неправда: коммит есть, он не выгружен. Ложное обвинение хуже
    пропуска, потому что учит обходить гейт.
    """
    done = subprocess.run(
        ["git", "rev-parse", "--is-shallow-repository"],
        cwd=ROOT, capture_output=True, text=True,
    )
    return done.stdout.strip() == "true"


def _check_links(path: Path, meta: dict) -> list[str]:
    """Ссылки на другие записи и на коммиты.

    Пути под .gitignore не проверяются намеренно: половина доказательной базы —
    видео, кэш и прогоны, которых в репозитории нет и не будет. Гейт, ругающийся
    на них, научат обходить, и он перестанет значить что-либо.
    """
    problems: list[str] = []
    shallow = repo_is_shallow()
    for ref in meta.get("supersedes", []):
        if not list(path.parent.glob(f"{ref}-*.md")):
            problems.append(f"{path.name}: supersedes {ref!r} — такой записи нет")
    for item in meta["evidence"]:
        if str(item).startswith("commit:"):
            if shallow:
                continue
            sha = str(item).split(":", 1)[1].strip()
            done = subprocess.run(
                ["git", "cat-file", "-e", f"{sha}^{{commit}}"],
                cwd=ROOT, capture_output=True,
            )
            if done.returncode != 0:
                problems.append(f"{path.name}: коммита {sha} нет в истории")
        elif str(item).startswith("file:"):
            rel = str(item).split(":", 1)[1].strip()
            if not (ROOT / rel).exists():
                problems.append(f"{path.name}: файла {rel} нет")
    return problems


def records(directory: Path) -> list[Path]:
    return sorted(p for p in directory.glob("*.md") if p.name != "README.md")


def render_index(directory: Path) -> str:
    """Каталог записей. Генерируется, руками не правится."""
    rows = []
    for path in records(directory):
        meta = json.loads(split_front(path.read_text(encoding="utf-8"))[0])
        rows.append(
            f"| [{path.stem.split('-', 1)[0]}]({path.name}) | {meta['title']} | "
            f"{meta['kind']} | {meta['authority']} | {meta['status']} | "
            f"{', '.join(meta['tags'])} |"
        )
    return (
        "# Память решений\n\n"
        "Каталог генерируется `tools/hooks/gate_memory.py --save`, руками не правится.\n"
        "Действующие запреты одной страницей — в [CLAUDE.md](../../CLAUDE.md).\n\n"
        "Домен здесь — **кто вправе отменить**: `заказчик` (агент только приносит\n"
        "замер и спрашивает), `замер` (отменяется замером не хуже исходного),\n"
        "`уклад` (правила работы, меняет владелец).\n\n"
        "| № | Решение | Жанр | Кто отменяет | Статус | Теги |\n"
        "|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )


def check_index(directory: Path) -> list[str]:
    """Каталог сверяется с набором файлов — как baseline с числами."""
    index = directory / "README.md"
    expected = render_index(directory)
    if not index.exists():
        return ["нет каталога docs/decisions/README.md: python3 "
                "tools/hooks/gate_memory.py --save"]
    if index.read_text(encoding="utf-8") != expected:
        return ["каталог разошёлся с записями. Пересобрать: python3 "
                "tools/hooks/gate_memory.py --save"]
    return []


def check_all(directory: Path) -> list[str]:
    if not directory.exists():
        return []
    problems: list[str] = []
    for path in records(directory):
        problems += check_record(path)
    return problems + check_index(directory)


def main(argv: list[str]) -> int:
    if not DECISIONS_DIR.exists():
        print("· памяти решений ещё нет — пропущено")
        return 0

    if "--save" in argv:
        (DECISIONS_DIR / "README.md").write_text(
            render_index(DECISIONS_DIR), encoding="utf-8"
        )
        print("каталог пересобран: docs/decisions/README.md")
        return 0

    problems = check_all(DECISIONS_DIR)
    if problems:
        print("✗ гейт памяти не пройден:", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1

    total = records(DECISIONS_DIR)
    unverified = [
        p.name for p in total
        if str(json.loads(split_front(p.read_text(encoding="utf-8"))[0])["verify"])
        .startswith("none:")
    ]
    print(f"✓ память: {len(total)} записей, замечаний нет")
    if unverified:
        # Не отказ: у отрицательного результата исполняемой проверки быть не
        # может. Но число видно, и оно не должно расти незаметно.
        print(f"  без исполняемой проверки: {len(unverified)} из {len(total)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
