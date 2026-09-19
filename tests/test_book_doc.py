"""Документ сборки книги не расходится с каталогом решений.

`docs/КНИГА.md` — рабочая инструкция: по ней собирается каждый следующий файл.
Обоснований она не пересказывает, а называет записи решений, и ровно тут её
подстерегает смерть: код меняется, запись появляется, документ остаётся
прежним. С `README.md` это уже произошло — в нём нет ни слова о книге, бланке и
поставке, хотя решений по ним двадцать одно.

Отсюда две проверки:

* **ссылка ведёт куда-то.** «Решение 091», которого нет, хуже отсутствия
  ссылки: читатель идёт искать обоснование и не находит его;
* **основание кода в документе названо.** Модуль сборки, который ссылается на
  решение, объявляет его своим основанием. Человек, собирающий книгу по
  документу, обязан видеть то же основание — иначе документ описывает не ту
  книгу, которую собирает код.

Вторая проверка нарочно грубая: она требует упоминания номера, а не пересказа.
Пересказывать и запрещено — принципы и записи живут в своих файлах.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOC = ROOT / "docs" / "КНИГА.md"
DECISIONS = ROOT / "docs" / "decisions"

# «решение 079», «решения 079, 080», «решениях 069 и 072» — все формы, какими
# запись называют в тексте. Номер без слова не считается: «страница 072» не
# ссылка.
_RUN = re.compile(r"решени[яеюйих]{1,2}((?:[\s,и]+\d{3})+)", re.IGNORECASE)
_NUMBER = re.compile(r"\d{3}")


def _cited(text: str) -> set[str]:
    return {n for run in _RUN.findall(text) for n in _NUMBER.findall(run)}


def named_in_doc() -> set[str]:
    return _cited(DOC.read_text(encoding="utf-8"))


def cited_by_code() -> dict[str, str]:
    """Решения, на которые ссылается код сборки книги, и где именно."""
    found: dict[str, str] = {}
    for path in sorted((ROOT / "src" / "paxcount" / "delivery").glob("*.py")):
        for number in _cited(path.read_text(encoding="utf-8")):
            found.setdefault(number, path.name)
    return found


def front_matter(path: Path) -> dict:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("+++"):
        return {}
    body = text.split("+++", 2)[1]
    return json.loads(body)


def test_the_document_exists():
    assert DOC.exists(), "инструкция сборки книги — не необязательное приложение"


def test_every_decision_named_in_the_doc_exists():
    files = {p.name[:3] for p in DECISIONS.glob("[0-9][0-9][0-9]-*.md")}
    unknown = sorted(named_in_doc() - files)
    assert not unknown, f"в документе названы несуществующие решения: {unknown}"


def test_every_decision_the_code_leans_on_is_named_in_the_doc():
    """Основание кода, о котором документ молчит, до человека не доедет."""
    named = named_in_doc()
    silent = {n: where for n, where in cited_by_code().items() if n not in named}
    assert not silent, (
        "код сборки ссылается на решения, которых нет в docs/КНИГА.md: "
        + "; ".join(f"{n} ({w})" for n, w in sorted(silent.items()))
    )


def test_a_decision_the_doc_retired_is_not_still_claimed_as_active():
    """Отменённое решение в документе — ссылка на правило, которого нет."""
    retired = []
    for number in sorted(named_in_doc()):
        found = list(DECISIONS.glob(f"{number}-*.md"))
        if found and front_matter(found[0]).get("status") == "отменено":
            retired.append(number)
    assert not retired, f"в документе названы отменённые решения: {retired}"


@pytest.mark.parametrize("section", [
    "Постоянное и переменное", "Вход", "Часы", "Выход", "Графы",
    "Порядок строк", "Цвета", "Ручной ввод", "Комментарий", "Портал",
    "расхождений", "Приёмка", "Чего сборка не делает",
])
def test_the_document_keeps_its_sections(section):
    """Раздел, выпавший из документа, — это правило, о котором забудут."""
    assert section in DOC.read_text(encoding="utf-8")
