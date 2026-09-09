#!/usr/bin/env python3
"""Синтаксические гейты: то, что проверяется по самому файлу, без запуска пайплайна.

Один и тот же код зовут оба хука, но по разным источникам:

- ``--staged`` (pre-commit) — содержимое из индекса (``git show :файл``), а не
  из рабочего дерева: иначе гейт пропускал бы правку, которая лежит на диске,
  но не попала в коммит, и наоборот — ругался бы на то, что в коммит не идёт.
- ``--rev <sha>`` (pre-push) — содержимое коммита, который уезжает на сервер.
  Это не дублирование: коммит могли сделать с ``--no-verify``, и тогда push —
  последний рубеж перед публикацией.

Никаких зависимостей: только стандартная библиотека, поэтому хук работает и
там, где окружение проекта не поднято.
"""

from __future__ import annotations

import ast
import json
import subprocess
import sys

# Кадры с лицами и номерами машин наружу не уходят (см. README, «Персональные
# данные»). .gitignore это уже закрывает, но `git add -f` его обходит, а цена
# ошибки необратима: опубликованное видео из истории уже не вернуть.
MEDIA_SUFFIXES = (".webm", ".mp4", ".ogv", ".mov", ".avi", ".mkv", ".m4v")
WEIGHTS_SUFFIXES = (".pt", ".pth", ".onnx", ".engine", ".safetensors")
# Порог на размер файла: в репозитории только исходники, разметка и документы.
# Всё, что крупнее, — почти наверняка данные, которым здесь не место.
MAX_BYTES = 2 * 1024 * 1024
# Образцы собираются из кусков намеренно: иначе этот файл содержал бы их
# целиком и гейт валил бы сам себя. Исключать файл по имени было бы хуже —
# переименование молча отключило бы проверку.
CONFLICT_MARKERS = ("<" * 7 + " ", ">" * 7 + " ")
_BEGIN = "-----BEGIN "
SECRET_HINTS = (
    _BEGIN + "RSA PRIVATE KEY",
    _BEGIN + "OPENSSH PRIVATE KEY",
    _BEGIN + "PRIVATE KEY",
    "ghp" + "_",
    "github" + "_pat_",
    "sk" + "-ant-",
)


def _git(*args: str) -> bytes:
    return subprocess.run(["git", *args], capture_output=True, check=True).stdout


def staged_sources() -> list[tuple[str, bytes]]:
    names = _git("diff", "--cached", "--name-only", "--diff-filter=ACM").decode()
    return [(p, _git("show", f":{p}")) for p in names.splitlines() if p.strip()]


def revision_sources(rev: str) -> list[tuple[str, bytes]]:
    names = _git("ls-tree", "-r", "--name-only", rev).decode()
    return [(p, _git("show", f"{rev}:{p}")) for p in names.splitlines() if p.strip()]


def inspect(files: list[tuple[str, bytes]]) -> list[str]:
    problems: list[str] = []
    for path, blob in files:
        low = path.lower()

        if low.endswith(MEDIA_SUFFIXES):
            problems.append(
                f"{path}: видео в репозитории. Кадры содержат лица и номера машин — "
                "наружу отдаются только числа (README, «Персональные данные»)"
            )
            continue
        if low.endswith(WEIGHTS_SUFFIXES):
            problems.append(f"{path}: веса модели не версионируются, их тянет uv/ultralytics")
            continue
        if path.endswith(".DS_Store"):
            problems.append(f"{path}: служебный файл macOS")
            continue
        if len(blob) > MAX_BYTES:
            problems.append(
                f"{path}: {len(blob) / 1e6:.1f} МБ — больше порога "
                f"{MAX_BYTES / 1e6:.0f} МБ; в репозитории только исходники и разметка"
            )
            continue

        try:
            text = blob.decode("utf-8")
        except UnicodeDecodeError:
            continue

        if any(m in text for m in CONFLICT_MARKERS):
            problems.append(f"{path}: остался маркер конфликта слияния")
        for hint in SECRET_HINTS:
            if hint in text:
                problems.append(f"{path}: похоже на секрет в открытом виде ({hint!r})")
                break

        if low.endswith(".py"):
            try:
                ast.parse(text, filename=path)
            except SyntaxError as exc:
                problems.append(f"{path}:{exc.lineno}: синтаксис — {exc.msg}")
            # Логирование в проекте идёт через runlog.setup_logger, а не print:
            # print не попадает в logs/paxcount.log и теряется при прогоне
            # (PRINCIPLES.md). В cli.py и webui печать в консоль — это интерфейс,
            # а не логирование, там rich.Console.
            if path.startswith("src/paxcount/"):
                for i, line in enumerate(text.splitlines(), 1):
                    if line.lstrip().startswith("print("):
                        problems.append(
                            f"{path}:{i}: print() в пайплайне — логировать через "
                            "runlog.setup_logger (PRINCIPLES.md)"
                        )

        if low.endswith(".json"):
            try:
                json.loads(text)
            except json.JSONDecodeError as exc:
                problems.append(f"{path}:{exc.lineno}: JSON не парсится — {exc.msg}")

    return problems


def main(argv: list[str]) -> int:
    if len(argv) >= 2 and argv[1] == "--rev":
        files = revision_sources(argv[2])
        where = f"коммит {argv[2][:8]}"
    else:
        files = staged_sources()
        where = "индекс"

    problems = inspect(files)
    if problems:
        print(f"✗ синтаксические гейты не пройдены ({where}):", file=sys.stderr)
        for p in problems:
            print(f"  {p}", file=sys.stderr)
        return 1
    print(f"✓ синтаксис: {len(files)} файлов, замечаний нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
