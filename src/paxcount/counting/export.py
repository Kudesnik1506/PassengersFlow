"""Пакеты кадров на диск — для счётной модели, работающей по подписке.

Клиент по ключу API (`client.py`) отдаёт кадры в теле запроса, и на диске они
не появляются вовсе. У заказчика ключа нет и не будет: счёт идёт по подписке,
а это значит, что кадры должен прочитать слепой счётчик — с диска, файлами.
Появляется каталог с лицами и номерами, которого при вызове по ключу не было.

Отсюда единственное правило этого модуля, и оно жёсткое: **писать кадры можно
только под `out/`**. Запрет 7 разрешает кадрам уходить в счётный API и
запрещает публиковать их — `out/` закрыт `.gitignore` целиком, всё остальное
нет. Путь проверяется, а не доверяется вызывающему: промах здесь необратим,
опубликованное из истории не вынуть.

Форму пакета модуль не выдумывает, а сохраняет: одна дверь — один каталог
(`packages.py`), секунда кадра — в имени файла, потому что именно это обещает
промпт. Дверь без кадров каталога не получает: отсутствие пакета означает «не
считали», и подменять его пустой папкой значит превращать невидимую дверь в
честный нулевой счёт.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..settings import OUT_DIR
from .packages import Package

MANIFEST_NAME = "задание.json"


class FramesOutsideOutDirError(RuntimeError):
    """Попытка записать кадры вне `out/` — запрет 7."""


@dataclass(frozen=True)
class WrittenPackage:
    """Пакет, лежащий на диске: чем он был и где теперь лежит."""

    variant: str
    visit: str
    door: int
    directory: Path
    manifest: Path
    frames: tuple[Path, ...]


def write_packages(
    packages: list[Package],
    root: Path,
    variant: str,
    *,
    prompt: str,
    visit: str = "",
    out_dir: Path = OUT_DIR,
) -> list[WrittenPackage]:
    """Кладёт пакеты под ``root`` и возвращает то, что легло.

    ``out_dir`` — не настройка, а точка подмены для тестов: в бою это `out/`.
    Вызывающий не может расширить разрешённое место, он может только назвать
    каталог внутри него.
    """
    _refuse_outside(root, out_dir)

    written: list[WrittenPackage] = []
    for package in packages:
        if not package.frames:
            continue
        key = visit or package.visit_key or str(package.visit_id)
        directory = root / variant / _safe(key) / f"д{package.door}"
        directory.mkdir(parents=True, exist_ok=True)

        frames: list[Path] = []
        for frame in package.frames:
            path = directory / frame_name(frame.t)
            path.write_bytes(frame.jpeg)
            frames.append(path)

        manifest = directory / MANIFEST_NAME
        manifest.write_text(
            json.dumps(
                {
                    "variant": variant,
                    "visit": key,
                    "visit_id": package.visit_id,
                    "door": package.door,
                    "camera": package.camera,
                    "frame_size": [package.width, package.height],
                    "prompt": prompt,
                    "frames": [
                        {"t": f.t, "file": frame_name(f.t)} for f in package.frames
                    ],
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
        written.append(WrittenPackage(
            variant=variant, visit=key, door=package.door, directory=directory,
            manifest=manifest, frames=tuple(frames),
        ))
    return written


def frame_name(t: float) -> str:
    """Имя кадра несёт его секунду от начала окна — так обещает промпт.

    Секунда дополняется нулями слева. Счётчик читает каталог, а каталог отдаёт
    имена по алфавиту: без дополнения кадр десятой секунды встаёт перед второй,
    лента идёт задом наперёд, и вход становится выходом молча.
    """
    return f"t{t:06.2f}.jpg"


def _refuse_outside(root: Path, out_dir: Path) -> None:
    root_abs = Path(root).resolve()
    allowed = Path(out_dir).resolve()
    if root_abs != allowed and allowed not in root_abs.parents:
        raise FramesOutsideOutDirError(
            f"кадры пишутся только под {allowed}, а не в {root_abs} — запрет 7"
        )


def _safe(key: str) -> str:
    """Ключ визита в имя каталога: двоеточия в путях живут плохо."""
    return key.replace(":", "-").replace("/", "-").replace("|", "_")
