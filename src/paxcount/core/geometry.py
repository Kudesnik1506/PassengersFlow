"""Геометрия линий и устойчивое определение пересечений.

Ключевое решение (№3 в плане): направление считается не по факту касания
линии, а по устойчивой смене стороны. Трек должен продержаться на новой
стороне ``hysteresis`` кадров подряд, иначе топчущийся в дверях человек даст
десяток событий вместо одного.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .types import Direction, Point


def side_of_line(p: tuple[float, float], a: Point, b: Point) -> int:
    """Знак стороны точки относительно направленной линии a->b."""
    cross = (b.x - a.x) * (p[1] - a.y) - (b.y - a.y) * (p[0] - a.x)
    if abs(cross) < 1e-9:
        return 0
    return 1 if cross > 0 else -1


def distance_to_segment(p: tuple[float, float], a: Point, b: Point) -> float:
    ax, ay, bx, by = a.x, a.y, b.x, b.y
    px, py = p
    dx, dy = bx - ax, by - ay
    if dx == 0 and dy == 0:
        return float(np.hypot(px - ax, py - ay))
    t = max(0.0, min(1.0, ((px - ax) * dx + (py - ay) * dy) / (dx * dx + dy * dy)))
    return float(np.hypot(px - (ax + t * dx), py - (ay + t * dy)))


def anchor_of(bbox: np.ndarray) -> tuple[float, float]:
    """Точка опоры человека — низ bbox по центру (решение №2 в плане).

    Центр bbox «плывёт» при перекрытии людьми друг друга, низ ведёт себя
    стабильнее: это проекция стоп на плоскость земли.
    """
    x1, y1, x2, y2 = bbox
    return float((x1 + x2) / 2), float(y2)


@dataclass
class TrackState:
    """Состояние одного трека относительно одной линии."""

    side: int = 0
    candidate_side: int = 0
    candidate_frames: int = 0
    last_anchor: tuple[float, float] | None = None
    last_frame: int = -1
    last_distance: float = 1e9
    seen_frames: int = 0
    # Взведён ли счётчик. После события трек обязан уйти за пределы полосы,
    # прежде чем сможет дать следующее: иначе идущий вдоль порога человек
    # даёт out-in-out-in на одном треке (наблюдалось на треке 388 в 04).
    armed: bool = True


@dataclass
class LineCounter:
    """Счётчик пересечений одной двери с гистерезисом.

    ``inside_sign`` — знак стороны, которая считается салоном. Переход
    наружу->салон это посадка (IN), обратный — высадка (OUT).
    """

    door_id: str
    inside_sign: int = -1
    hysteresis: int = 3
    max_door_distance: float = 1e9
    states: dict[int, TrackState] = field(default_factory=dict)

    def update(
        self,
        track_id: int,
        bbox: np.ndarray,
        a: Point,
        b: Point,
        frame_idx: int,
    ) -> Direction | None:
        """Скармливает трек и возвращает событие, если оно произошло."""
        anchor = anchor_of(bbox)
        side = side_of_line(anchor, a, b)
        dist = distance_to_segment(anchor, a, b)
        st = self.states.setdefault(track_id, TrackState())
        st.last_anchor = anchor
        st.last_frame = frame_idx
        st.last_distance = dist
        st.seen_frames += 1
        if not st.armed and dist > self.max_door_distance:
            st.armed = True

        if st.side == 0:
            st.side = side
            return None
        if side == 0 or side == st.side:
            st.candidate_side = 0
            st.candidate_frames = 0
            return None

        # Сторона сменилась — копим подтверждение.
        if side == st.candidate_side:
            st.candidate_frames += 1
        else:
            st.candidate_side = side
            st.candidate_frames = 1

        if st.candidate_frames < self.hysteresis:
            return None
        if dist > self.max_door_distance:
            # Пересёк линию, но далеко от дверного проёма — это не посадка.
            st.side = side
            st.candidate_side = 0
            st.candidate_frames = 0
            return None

        st.side = side
        st.candidate_side = 0
        st.candidate_frames = 0
        if not st.armed:
            return None
        st.armed = False
        return Direction.IN if side == self.inside_sign else Direction.OUT

    def resolve_disappearance(
        self, track_id: int, current_frame: int, gone_after: int
    ) -> Direction | None:
        """Трек пропал внутри дверного проёма — считаем это завершённым проходом.

        Для наружной камеры человек, зашедший в салон, физически исчезает из
        кадра за корпусом. Без этой ветки посадки не считаются вовсе.
        """
        st = self.states.get(track_id)
        if st is None or st.last_anchor is None:
            return None
        if current_frame - st.last_frame != gone_after:
            return None
        if st.last_distance > self.max_door_distance:
            return None
        if st.seen_frames < self.hysteresis:
            return None
        if st.side == self.inside_sign:
            return None
        # Пропал вплотную к двери со стороны улицы — вошёл.
        st.side = self.inside_sign
        return Direction.IN
