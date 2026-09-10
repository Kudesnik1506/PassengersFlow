"""Окна счёта для мультимодальной модели: где активность, во что она обойдётся.

Конвейер больше не обязан считать людей (решение 020) — его работа сузилась до
поиска транспорта и окна активности у дверей. `activity_window` в `zonecount.py`
уже делает само сужение; этот модуль ничего не пересчитывает заново (принцип 5),
а добавляет ровно то, чего не было: оценку размера окна и признак, что визит
нужно показать человеку на проверке, а не считать молча.

Кадры здесь не читаются и не пишутся — только числа по уже готовому кэшу
треков. Запись кадров в пакет — следующий, отдельный шаг (`packages.py`).
"""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from .core.trackdata import TrackData
from .core.types import VehicleVisit, VideoConfig
from .doors import fallback_doors
from .settings import PACKAGE_FPS, PACKAGE_MAX_WIDTH, TOKENS_PER_PIXEL_DIVISOR
from .visits import Scene
from .zonecount import activity_window


@dataclass(frozen=True)
class Window:
    """Окно счёта одного визита: границы, представительный bbox, предупреждения.

    `box` и `person_px` — оценочные величины для бюджета и очереди проверки,
    не источник правды для самого счёта: тот остаётся в `Scene.boxes_for`.
    """

    visit_id: int
    t0: float
    t1: float
    box: tuple[float, float, float, float] | None
    person_px: float | None
    overlap: bool

    @property
    def duration(self) -> float:
        return max(self.t1 - self.t0, 0.0)

    def n_frames(self, fps: float = PACKAGE_FPS) -> int:
        if self.duration <= 0:
            return 0
        return max(1, math.ceil(self.duration * fps))

    def tokens_estimate(
        self, fps: float = PACKAGE_FPS, max_width: float = PACKAGE_MAX_WIDTH
    ) -> int:
        """Грубая оценка токенов на один прогон: (кроп / 750) × число кадров.

        Кроп масштабируется к `max_width`, как это сделает упаковка кадров —
        оценка должна отражать то, что реально уйдёт модели, а не полный кадр
        целиком (окно у дверей на боевой записи может быть шире Full HD).
        """
        if self.box is None:
            return 0
        w = self.box[2] - self.box[0]
        h = self.box[3] - self.box[1]
        if w <= 0 or h <= 0:
            return 0
        if w > max_width:
            scale = max_width / w
            w, h = w * scale, h * scale
        return int(round((w * h / TOKENS_PER_PIXEL_DIVISOR) * self.n_frames(fps)))


def _window_box(
    boxes_by_frame: dict[int, np.ndarray], data: TrackData, t0: float, t1: float
) -> tuple[float, float, float, float] | None:
    """Представительный bbox ТС внутри окна — медиана по наблюдениям.

    Не квантиль наружу, как `visits.canonical_box`: там цель — не занизить
    границу двери, здесь — честная оценка размера кропа для бюджета, где
    систематическое завышение так же вредно, как и занижение.
    """
    values = [
        boxes_by_frame[f.frame_idx]
        for f in data.frames
        if t0 <= f.ts <= t1 and f.frame_idx in boxes_by_frame
    ]
    if not values:
        return None
    arr = np.asarray(values, dtype=float)
    x0, y0, x1, y1 = np.median(arr, axis=0)
    return float(x0), float(y0), float(x1), float(y1)


def _person_px(data: TrackData, t0: float, t1: float) -> float | None:
    """Медианная высота bbox человека внутри окна.

    Ниже `settings.MIN_PERSON_PX` — сигнал «мелко»: на таком масштабе человека
    не различить, и это задача камеры, а не точности модели. Используется,
    чтобы такие визиты сразу помечались под проверку, а не терялись в потоке.
    """
    heights: list[float] = []
    for f in data.frames:
        if not (t0 <= f.ts <= t1):
            continue
        for box in f.person_boxes:
            heights.append(float(box[3] - box[1]))
    if not heights:
        return None
    return float(np.median(heights))


def _has_overlap(visit: VehicleVisit, others: list[VehicleVisit]) -> bool:
    """Пересекается ли визит по времени с другим визитом сцены.

    Два ТС у остановки одновременно — оба идут в очередь проверки: кроп
    каждого может задеть другую машину, риск двойного счёта реален, а
    единственная дешёвая защита сегодня — показать это человеку, а не
    выяснять автоматически, кто из них кто.
    """
    for other in others:
        if other.visit_id == visit.visit_id:
            continue
        if visit.arrival_ts <= other.departure_ts and other.arrival_ts <= visit.departure_ts:
            return True
    return False


def build_windows(data: TrackData, config: VideoConfig, scene: Scene) -> list[Window]:
    """Окно активности плюс оценка бюджета на каждый визит сцены.

    Выбор дверных зон — `config.doors_for(aspect) or fallback_doors()` —
    существует здесь в одном месте: без него ручная разметка с пустым
    `doors` (например, отменённый набор) тихо вернула бы окно во весь визит
    вместо честного фолбэка (полоса ног на весь корпус), а конвейер и человек,
    читающий вывод, разошлись бы в том, почему окно вдруг стало огромным.
    """
    windows: list[Window] = []
    for visit in scene.visits:
        boxes_by_frame = scene.boxes_for(visit)
        aspect = None
        if boxes_by_frame:
            arr = np.asarray(list(boxes_by_frame.values()), dtype=float)
            w = arr[:, 2] - arr[:, 0]
            h = np.maximum(arr[:, 3] - arr[:, 1], 1.0)
            aspect = float(np.median(w / h))
        specs = config.doors_for(aspect) or fallback_doors()
        t0, t1 = activity_window(data, specs, visit, boxes_by_frame)
        windows.append(
            Window(
                visit_id=visit.visit_id,
                t0=t0,
                t1=t1,
                box=_window_box(boxes_by_frame, data, t0, t1),
                person_px=_person_px(data, t0, t1),
                overlap=_has_overlap(visit, scene.visits),
            )
        )
    return windows
