"""Дубликат границ версий между [project] и [dependency-groups].test — под надзором.

Тестовая группа перечисляет numpy, pydantic, pandas и rich заново, чтобы CI не
ставил torch ради юнит-тестов. Это осознанное отступление от принципа 2, и
единственное, что делает его допустимым, — проверка: расхождение копий должно
падать здесь, а не проявляться разными числами в CI и локально.
"""

from __future__ import annotations

import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def specs(entries: list[str]) -> dict[str, str]:
    out = {}
    for entry in entries:
        if "@" in entry:  # git-зависимость, границ версий не имеет
            continue
        for sep in (">=", "==", "~=", ">"):
            if sep in entry:
                name, version = entry.split(sep, 1)
                out[name.strip()] = f"{sep}{version.strip()}"
                break
    return out


def test_test_group_matches_project_dependencies():
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    project = specs(pyproject["project"]["dependencies"])
    group = specs(pyproject["dependency-groups"]["test"])

    shared = set(project) & set(group)
    assert shared, "тестовая группа не пересекается с зависимостями проекта — проверять нечего"
    mismatched = {name: (project[name], group[name]) for name in shared
                  if project[name] != group[name]}
    assert not mismatched, f"границы версий разошлись: {mismatched}"
