"""Значения из `.env` — одно чтение на всех, кому они нужны.

В `.env` лежит то, чего не должно быть ни в коде, ни в публичном репозитории:
учётка государственного портала и фамилия расшифровщика — имя живого человека
(решение 016). Файл закрыт правилом запрета и здесь только читается.

Окружение важнее файла: запуск в CI или на чужой машине не должен требовать
класть пароль на диск.
"""

from __future__ import annotations

import os
from pathlib import Path

ENV_FILE = Path(".env")


def values(env_path: Path = ENV_FILE) -> dict[str, str]:
    """Настройки: сначала файл, поверх — окружение.

    Отсутствующий файл не ошибка: часть значений может прийти окружением, а
    чего именно не хватило, решает тот, кто спрашивает, — он один знает, без
    чего не может работать.
    """
    found: dict[str, str] = {}
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            if "=" in line and not line.strip().startswith("#"):
                key, _, value = line.partition("=")
                found[key.strip()] = value.strip()
    found.update({k: v for k, v in os.environ.items() if k in found or k.isupper()})
    return found
