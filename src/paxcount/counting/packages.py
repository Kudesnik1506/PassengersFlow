"""Нарезка окна визита в пакеты кадров — по одной двери в пакет.

Форма пакета повторяет ручную методику, а не удобство вызова: она куплена
ошибками разбора боевых записей.

**Одна дверь — один пакет.** Ручной счёт ведётся по дверям, и строка
закрывается, когда журналов столько же, сколько дверей в кадре. Модель,
которой показали весь борт разом, отвечает одним числом — проверить его по
дверям нечем, а именно подверная сверка ловила и потерянную дверь, и
посчитанного дважды человека.

**Кроп берётся с запасом по обе стороны проёма** (`settings.CROP_MARGIN_PX`).
Узкий кроп по самому проёму — замеренная причина недосчёта: человек, идущий
вдоль борта, появляется в таком кадре уже внутри проёма, и вход от прохода
мимо не отличить. Событие входа — пересечение проёма, а чтобы его увидеть,
нужно видеть обе стороны.

**Дверь за кадром пакета не получает вовсе.** Отсутствие пакета означает «не
считали», и это не то же самое, что ноль: ноль — это «посмотрели, там пусто».
Смешать их значит превратить невидимую дверь в честный нулевой счёт.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass

import numpy as np

from ..settings import (
    CROP_MARGIN_PX,
    PACKAGE_FPS,
    PACKAGE_MAX_WIDTH,
    TOKENS_PER_PIXEL_DIVISOR,
)
from ..truth import DoorLayout
from ..windows import Window

JPEG_QUALITY = 85


@dataclass(frozen=True)
class PackageFrame:
    """Один кадр пакета. `t` — секунда от начала окна, как обещает промпт."""

    t: float
    jpeg: bytes


@dataclass(frozen=True)
class Package:
    """Кадры одной двери одного визита — то, что уходит в модель за один раз."""

    visit_id: int
    door: int
    visit_key: str
    camera: str
    width: int
    height: int
    frames: tuple[PackageFrame, ...]

    def tokens_estimate(self) -> int:
        """Оценка токенов по формуле Claude Vision: (ширина × высота) / 750."""
        per_frame = self.width * self.height / TOKENS_PER_PIXEL_DIVISOR
        return int(round(per_frame * len(self.frames)))


def crop_box(
    opening_px: tuple[float, float, float, float],
    frame_size: tuple[int, int],
    margin_px: float = CROP_MARGIN_PX,
) -> tuple[float, float, float, float]:
    """Прямоугольник кропа вокруг проёма, с запасом и в границах кадра."""
    width, height = frame_size
    x0, y0, x1, y1 = opening_px
    return (
        max(0.0, x0 - margin_px),
        max(0.0, y0 - margin_px),
        min(float(width), x1 + margin_px),
        min(float(height), y1 + margin_px),
    )


def build_packages(
    frames: Iterator[tuple[int, float, np.ndarray]],
    window: Window,
    layout: DoorLayout | None = None,
    fps: float = PACKAGE_FPS,
    max_width: float = PACKAGE_MAX_WIDTH,
) -> list[Package]:
    """Пакеты кадров по одному на каждую видимую дверь визита.

    `frames` — лента кадров видео (`core.video.frames`): пара «номер, секунда,
    картинка». Лента читается ровно один раз: на боевой записи она длинная, и
    второй проход по ней стоит столько же, сколько первый.

    Без разметки дверей (`layout=None`) пакет строится один — по рамке ТС из
    окна. Это фолбэк для роликов без разметки, а не рабочий режим: счёт по
    нему нельзя разложить по дверям и нельзя свести с таблицей 3.
    """
    doors = _visible_doors(layout, window)
    if not doors:
        return []

    wanted = _sample_times(window, fps)
    if not wanted:
        return [_empty(window, layout, door, box) for door, box in doors]

    collected: dict[int, list[PackageFrame]] = {door: [] for door, _ in doors}
    frame_size: tuple[int, int] | None = None
    index = 0
    for _frame_idx, ts, picture in frames:
        if index >= len(wanted):
            break
        if ts < window.t0 + wanted[index]:
            continue
        if frame_size is None:
            frame_size = (picture.shape[1], picture.shape[0])
        for door, opening in doors:
            crop = _crop(picture, crop_box(opening, frame_size), max_width)
            if crop is None:
                continue
            collected[door].append(
                PackageFrame(t=round(ts - window.t0, 3), jpeg=_encode(crop))
            )
        index += 1

    packages: list[Package] = []
    for door, opening in doors:
        got = collected[door]
        if not got:
            packages.append(_empty(window, layout, door, opening))
            continue
        size = _decoded_size(got[0])
        packages.append(
            Package(
                visit_id=window.visit_id, door=door,
                visit_key=layout.visit_key if layout else "",
                camera=layout.camera if layout else "",
                width=size[0], height=size[1], frames=tuple(got),
            )
        )
    return packages


def _visible_doors(
    layout: DoorLayout | None, window: Window
) -> list[tuple[int, tuple[float, float, float, float]]]:
    if layout is None:
        return [(0, window.box)] if window.box else []
    return [
        (d.n_from_nose, d.opening_px)
        for d in sorted(layout.doors, key=lambda d: d.n_from_nose)
        if d.in_frame and d.opening_px is not None
    ]


def _sample_times(window: Window, fps: float) -> list[float]:
    """Секунды от начала окна, на которых берутся кадры."""
    if window.duration <= 0 or fps <= 0:
        return []
    step = 1.0 / fps
    count = int(window.duration / step) + 1
    return [i * step for i in range(count)]


def _crop(
    picture: np.ndarray, box: tuple[float, float, float, float], max_width: float
) -> np.ndarray | None:
    """Вырезает и, если нужно, уменьшает кроп.

    `cv2` импортируется внутри, а не в шапке модуля: тестовая группа проекта
    ставится без OpenCV (только numpy, pydantic, pandas, rich), и геометрия
    кропа с оценкой стоимости обязаны проверяться и там. Пиксели трогает
    только этот слой — он и платит за зависимость.
    """
    import cv2

    x0, y0, x1, y1 = (int(round(v)) for v in box)
    crop = picture[max(0, y0):max(0, y1), max(0, x0):max(0, x1)]
    if crop.size == 0:
        return None
    if crop.shape[1] > max_width:
        scale = max_width / crop.shape[1]
        crop = cv2.resize(
            crop, (int(max_width), max(1, int(round(crop.shape[0] * scale))))
        )
    return crop


def _encode(crop: np.ndarray) -> bytes:
    import cv2

    ok, buffer = cv2.imencode(".jpg", crop, [cv2.IMWRITE_JPEG_QUALITY, JPEG_QUALITY])
    if not ok:
        raise RuntimeError("кроп не закодировался в JPEG")
    return buffer.tobytes()


def _decoded_size(frame: PackageFrame) -> tuple[int, int]:
    import cv2

    picture = cv2.imdecode(np.frombuffer(frame.jpeg, dtype=np.uint8), cv2.IMREAD_COLOR)
    return (picture.shape[1], picture.shape[0])


def _empty(
    window: Window, layout: DoorLayout | None, door: int,
    opening: tuple[float, float, float, float],
) -> Package:
    width = int(round(opening[2] - opening[0] + 2 * CROP_MARGIN_PX))
    height = int(round(opening[3] - opening[1] + 2 * CROP_MARGIN_PX))
    return Package(
        visit_id=window.visit_id, door=door,
        visit_key=layout.visit_key if layout else "",
        camera=layout.camera if layout else "",
        width=width, height=height, frames=(),
    )
