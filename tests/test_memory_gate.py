"""Гейт памяти решений: что он обязан ловить.

Память протухает тише, чем код: неверная запись не роняет прогон, она молча уводит
следующую сессию не туда. Поэтому всё, что можно проверить кодом, проверяется
кодом (принцип 7): закрытые словари, разбор JSON, сверка каталога с набором файлов.

Проверка зовёт функции гейта, а не пересказывает его правила (принцип 5): вторая
реализация того же правила однажды разойдётся с первой, и зелёный тест перестанет
что-либо гарантировать о том, что реально блокирует push.
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "tools" / "hooks"))

import gate_memory  # noqa: E402

GOOD = """+++
{
  "title": "Не менять детектор",
  "date": "2026-09-09",
  "kind": "тупик",
  "authority": "замер",
  "status": "действует",
  "tags": ["трекинг"],
  "verify": "none: отрицательный результат, тестом не сторожится",
  "invalidates_on": ["узкое место сместилось на детекцию"],
  "evidence": ["measurement: 29 / 30 / 29 рамок на кадрах 557-565"],
  "origin": "задним числом",
  "supersedes": []
}
+++

Не менять детектор.

## Почему
Три модели дали 29, 30 и 29 рамок — разницы нет.
"""


def write(tmp_path: Path, body: str, name: str = "015-ne-menyat-detektor.md") -> Path:
    path = tmp_path / name
    path.write_text(body, encoding="utf-8")
    return path


def test_correct_record_passes(tmp_path):
    assert gate_memory.check_record(write(tmp_path, GOOD)) == []


def test_broken_json_is_caught(tmp_path):
    broken = GOOD.replace('"kind": "тупик",', '"kind": тупик,')
    problems = gate_memory.check_record(write(tmp_path, broken))
    assert any("JSON" in p for p in problems)


def test_unknown_tag_is_caught(tmp_path):
    """Открытый словарь тегов ломает поиск молча.

    В разных сессиях появились бы «двери», «дверная-зона», «door-zone» — и
    `grep` по тегу перестал бы находить половину записей, ничего не сообщив.
    """
    problems = gate_memory.check_record(
        write(tmp_path, GOOD.replace('"tags": ["трекинг"]', '"tags": ["door-zone"]'))
    )
    assert any("тег" in p for p in problems)


def test_unknown_invalidation_condition_is_caught(tmp_path):
    """Условие пересмотра — тоже закрытый словарь: по нему ищут grep'ом."""
    problems = gate_memory.check_record(
        write(tmp_path, GOOD.replace(
            '["узкое место сместилось на детекцию"]', '["когда-нибудь потом"]'))
    )
    assert any("условие пересмотра" in p for p in problems)


def test_unknown_authority_is_caught(tmp_path):
    problems = gate_memory.check_record(
        write(tmp_path, GOOD.replace('"authority": "замер"', '"authority": "техника"'))
    )
    assert any("authority" in p for p in problems)


def test_missing_field_is_caught(tmp_path):
    problems = gate_memory.check_record(
        write(tmp_path, GOOD.replace('  "verify": "none: отрицательный результат, '
                                     'тестом не сторожится",\n', ""))
    )
    assert any("verify" in p for p in problems)


def test_link_to_readme_anchor_is_caught(tmp_path):
    """Стрелка одна: README ссылается на записи, записи на README — никогда.

    Заголовки README меняются, якорь протухает молча, и запись начинает вести
    в никуда.
    """
    problems = gate_memory.check_record(
        write(tmp_path, GOOD + "\nПодробнее в [README](README.md#метрики).\n")
    )
    assert any("README" in p for p in problems)


def test_too_long_record_is_caught(tmp_path):
    """Потолок нужен, чтобы пятнадцать записей читались за раз."""
    problems = gate_memory.check_record(
        write(tmp_path, GOOD + "\n".join(f"строка {i}" for i in range(80)))
    )
    assert any("длиннее" in p for p in problems)


def test_deferred_record_needs_a_revision_condition(tmp_path):
    """«Отложено» без условия возврата равно «никогда» — и это неправда."""
    deferred = GOOD.replace('"status": "действует"', '"status": "отложено"').replace(
        '"invalidates_on": ["узкое место сместилось на детекцию"]', '"invalidates_on": []'
    )
    problems = gate_memory.check_record(write(tmp_path, deferred))
    assert any("отложено" in p for p in problems)


def test_filename_must_be_ascii(tmp_path):
    """Кириллица в имени: macOS пишет NFD, Linux в CI читает NFC.

    Файл есть локально и отсутствует в CI — сверка каталога с набором файлов
    сломалась бы именно там, где её никто не смотрит.
    """
    problems = gate_memory.check_record(write(tmp_path, GOOD, "015-детектор.md"))
    assert any("ASCII" in p for p in problems)


def test_index_mismatch_is_reported(tmp_path):
    """Каталог сверяется с набором файлов, как baseline — с числами."""
    write(tmp_path, GOOD)
    (tmp_path / "README.md").write_text("# Пусто\n", encoding="utf-8")
    problems = gate_memory.check_index(tmp_path)
    assert problems
    assert any("gate_memory" in p for p in problems)


def test_regenerated_index_passes_its_own_check(tmp_path):
    write(tmp_path, GOOD)
    (tmp_path / "README.md").write_text(gate_memory.render_index(tmp_path), encoding="utf-8")
    assert gate_memory.check_index(tmp_path) == []


def test_real_memory_passes_the_gate():
    """Настоящие записи проекта обязаны проходить собственный гейт."""
    assert gate_memory.check_all(ROOT / "docs" / "decisions") == []


@pytest.mark.parametrize("field", ["kind", "authority", "status"])
def test_vocabularies_are_non_empty(field):
    assert gate_memory.VOCABULARIES[field]


# ---- Мелкий клон: проверять нечего, но и врать нельзя ---------------------
#
# actions/checkout по умолчанию выгружает одну ревизию. Ссылки на прежние
# коммиты в такой копии не разрешаются — и гейт сообщал «коммита нет в истории»,
# что просто неправда: коммит есть, он не выгружен. Ложное обвинение хуже
# пропуска: оно учит обходить гейт.


def test_shallow_clone_does_not_accuse_of_missing_commits(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_memory, "repo_is_shallow", lambda: True)
    record = GOOD.replace(
        '"evidence": ["measurement: 29 / 30 / 29 рамок на кадрах 557-565"]',
        '"evidence": ["commit:0000000"]',
    )
    assert gate_memory.check_record(write(tmp_path, record)) == []


def test_full_clone_still_checks_commits(tmp_path, monkeypatch):
    monkeypatch.setattr(gate_memory, "repo_is_shallow", lambda: False)
    record = GOOD.replace(
        '"evidence": ["measurement: 29 / 30 / 29 рамок на кадрах 557-565"]',
        '"evidence": ["commit:0000000"]',
    )
    problems = gate_memory.check_record(write(tmp_path, record))
    assert any("0000000" in p for p in problems)
