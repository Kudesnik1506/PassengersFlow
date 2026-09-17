"""Гейт синтаксиса: что он обязан увидеть в индексе.

Гейт читает список файлов у git, а git по умолчанию экранирует всё, что вне
ASCII: `«имя — с тире».json` приезжает как `"имя \\342\\200\\224 ..."`. Такой
путь не открывается, гейт падает с трассировкой посреди коммита, и человек
видит не «вот ошибка в файле», а поломку инструмента.

Поймано боевым коммитом: в разметке дверей ключ визита содержит длинное тире
(«борт неизвестен»), и оно попадает в имя файла. Проверка зовёт функцию гейта
на настоящем репозитории — пересказывать правила git своими словами здесь
бессмысленно, ломается как раз стык с ним.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "hooks"))

import gate_syntax  # noqa: E402

TRICKY = "разметка — без борта.json"


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)


@pytest.fixture
def repo(tmp_path, monkeypatch) -> Path:
    """Пустой репозиторий с одним файлом в индексе, имя вне ASCII."""
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "t@example.com")
    _git(tmp_path, "config", "user.name", "t")
    (tmp_path / TRICKY).write_text('{"двери": []}\n', encoding="utf-8")
    _git(tmp_path, "add", TRICKY)
    monkeypatch.chdir(tmp_path)
    return tmp_path


def test_staged_file_with_non_ascii_name_is_read_not_crashed(repo):
    """Имя вне ASCII — обычный файл, а не повод уронить коммит трассировкой."""
    staged = gate_syntax.staged_sources()
    assert [name for name, _ in staged] == [TRICKY]
    assert b"\xd0\xb4\xd0\xb2\xd0\xb5\xd1\x80\xd0\xb8" in staged[0][1]
