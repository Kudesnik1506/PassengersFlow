"""Контракт бэкенда подсчёта.

Всё, что умеет считать людей, реализует эту сигнатуру. Именно она делает
возможным честное сравнение готового решения, своего пайплайна и гибрида.
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol

from ..core.types import RunResult, VideoConfig


class CountingBackend(Protocol):
    name: str

    def run(
        self,
        video: Path,
        config: VideoConfig,
        debug_path: Path | None = None,
        stride: int = 1,
    ) -> RunResult: ...
