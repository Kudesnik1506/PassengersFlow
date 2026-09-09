"""Единая точка логирования: stdout плюс файл, один формат.

Пайплайн запускается и вручную, и фоном из веб-UI. Фоновый прогон без лога
неотличим от зависшего, поэтому все модули пишут через этот логгер, а не через
print и не через собственные обёртки.
"""

from __future__ import annotations

import logging
import os
import sys
from pathlib import Path

from .settings import LOG_DIR

FORMAT = "[%(asctime)s] %(levelname)s %(name)s: %(message)s"
DATEFMT = "%Y-%m-%d %H:%M:%S"
_configured = False


def setup_logger(name: str = "paxcount", level: int = logging.INFO) -> logging.Logger:
    global _configured
    root = logging.getLogger("paxcount")
    if not _configured:
        LOG_DIR.mkdir(parents=True, exist_ok=True)
        formatter = logging.Formatter(FORMAT, datefmt=DATEFMT)

        stream = logging.StreamHandler(sys.stdout)
        stream.setFormatter(formatter)
        root.addHandler(stream)

        file_handler = logging.FileHandler(LOG_DIR / "paxcount.log", encoding="utf-8")
        file_handler.setFormatter(formatter)
        root.addHandler(file_handler)

        root.setLevel(level)
        root.propagate = False
        _configured = True
    return logging.getLogger(name if name.startswith("paxcount") else f"paxcount.{name}")


def unbuffered_env() -> dict[str, str]:
    """Окружение для фоновых python-процессов: без этого вывод теряется молча."""
    return {**os.environ, "PYTHONUNBUFFERED": "1"}
