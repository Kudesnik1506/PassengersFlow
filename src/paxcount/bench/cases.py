"""Случаи сравнения методов: кадр, рамка кузова, эталонные проёмы.

Вход у всех сравниваемых методов один и тот же — кадр и рамка кузова на нём.
Рамка берётся из ручной разметки, а не из детектора, и это не временная мера:
кэша детекции по боевым записям нет (решение 022 — детекция боевого ролика
стоит часов), а прогнать детектор ради рамки значило бы сравнивать методы на
разных входах и списывать разницу рамок на разницу методов.

Разметка, из которой случая не получилось, не пропадает молча. Молчаливый
пропуск даёт таблицу, построенную на меньшем числе машин, чем думает
читающий, — а на выборке в пять машин одна пропавшая меняет всё.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ..delivery.model import DOORS_BY_SIZE, VehicleSize
from ..truth import DoorLayout, load_door_layout

Box = tuple[float, float, float, float]


@dataclass(frozen=True)
class Case:
    """Один размеченный визит как задача для метода локализации дверей."""

    visit_key: str
    camera: str
    video: str
    frame_idx: int
    frame_size: tuple[int, int]
    body_px: Box
    size: VehicleSize
    orientation: str
    # Только видимые в кадре проёмы: дверь за краем кадра метод найти не мог.
    doors_visible: tuple[Box, ...]
    # Сколько дверей у машины по таблице 3 — знаменатель арифметики, а не меры.
    doors_total: int

    @property
    def truncated(self) -> tuple[bool, bool]:
        """Упирается ли кузов в левый и правый края кадра."""
        from ..settings import EDGE_TOUCH_PX

        return (self.body_px[0] <= EDGE_TOUCH_PX,
                self.body_px[2] >= self.frame_size[0] - EDGE_TOUCH_PX)


def case_from_layout(layout: DoorLayout) -> tuple[Case | None, str | None]:
    """Случай из разметки визита. Вторым — причина, по которой его не вышло."""
    if layout.body_px is None:
        return None, (
            f"{layout.visit_key}: не размечена рамка кузова — у методов не будет "
            "входа, а сравнивать их на разных рамках нельзя"
        )
    visible = tuple(d.opening_px for d in layout.doors
                    if d.in_frame and d.opening_px is not None)
    if not visible:
        return None, (
            f"{layout.visit_key}: ни одной двери в кадре — сравнивать не с чем"
        )
    return Case(
        visit_key=layout.visit_key,
        camera=layout.camera,
        video=layout.video,
        frame_idx=layout.frame_idx,
        frame_size=layout.frame_size,
        body_px=layout.body_px,
        size=layout.size,
        orientation=layout.orientation,
        doors_visible=visible,
        doors_total=DOORS_BY_SIZE.get(layout.size, len(layout.doors)),
    ), None


def load_cases(
    root: Path,
    cameras: "frozenset[str] | set[str] | None" = None,
) -> tuple[list[Case], list[str]]:
    """Все случаи из каталога разметки и список того, что случаем не стало.

    `cameras` — какие камеры брать. Политику камер загрузчик не знает сам:
    решает вызывающий, а здесь она лишь применяется и называется в пропусках.
    """
    if not root.is_dir():
        return [], []
    cases: list[Case] = []
    skipped: list[str] = []
    for path in sorted(root.rglob("*.json")):
        try:
            layout = load_door_layout(path)
        except ValueError as exc:
            skipped.append(f"{path.name}: разметка не читается — {exc}")
            continue
        if cameras is not None and layout.camera not in cameras:
            skipped.append(
                f"{layout.visit_key}: камера {layout.camera} не считает "
                "(решение 030) — в сравнение не идёт"
            )
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            skipped.append(problem or f"{path.name}: случай не собран")
        else:
            cases.append(case)
    cases.sort(key=lambda c: (c.camera, c.video, c.frame_idx))
    return cases, skipped
