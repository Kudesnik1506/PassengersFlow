"""Сборка `DoorLayout` из кандидатов автопредложения: арбитр — известное K.

`doorprop` предлагает проёмы двумя независимыми методами, но ни один из них не
знает, сколько дверей должно быть. Это знание приходит не из пикселей, а из
справочника: размер ТС в выгрузке оператора → число дверей по таблице 3
инструкции. Самая надёжная величина в задаче не может не участвовать в выборе,
поэтому она и назначена арбитром: ровно K кандидатов, упорядоченных от носа.

Главное различение модуля — между двумя случаями, которые снаружи выглядят
одинаково («нашли меньше, чем ожидали»):

* корпус обрезан краем кадра → недостающая дверь физически за кадром, это
  законный `in_frame=False`, из которого потом растёт код 1-8 в таблице;
* корпус виден целиком → дверь не нашлась по вине метода, а не съёмки.

Второй случай нельзя записывать как «дверь не видна»: тогда наш промах
превратится в законное оправдание недосчёта, уйдёт в отчёт кодом, и никто
никогда не узнает, что дверь была в кадре. Поэтому модуль возвращает список
замечаний рядом с разметкой, а не одну лишь разметку.
"""

from __future__ import annotations

from ..delivery.model import DOORS_BY_SIZE, VehicleSize
from ..settings import EDGE_TOUCH_PX
from ..truth import DoorLayout, DoorLayoutEntry
from . import DoorCandidate

NOSE_LEFT = "нос-слева"
NOSE_RIGHT = "нос-справа"


def layout_from_candidates(
    candidates: list[DoorCandidate],
    size: VehicleSize,
    visit_key: str,
    camera: str,
    orientation: str,
    box_px: tuple[float, float, float, float],
    frame_size: tuple[int, int],
    video: str,
    frame_idx: int,
) -> tuple[DoorLayout | None, list[str]]:
    """Разметка визита по кандидатам и известному числу дверей.

    Возвращает пару «разметка, замечания». Замечания пустые — разметку можно
    брать в работу; непустые — она требует человека, даже если сама разметка
    при этом построена.
    """
    problems: list[str] = []
    expected = DOORS_BY_SIZE.get(size)
    if expected is None:
        return None, [
            f"размер {size.value}: число дверей по таблице 3 неизвестно "
            "(у рельсового транспорта размер считается вагонами) — "
            "арбитру нечем выбирать кандидатов"
        ]

    chosen = sorted(candidates, key=lambda c: c.score, reverse=True)[:expected]
    if len(candidates) > expected:
        problems.append(
            f"кандидатов {len(candidates)}, дверей по таблице 3 — {expected}: "
            f"лишние отброшены по score, проверить, не принято ли окно за дверь"
        )

    chosen.sort(key=lambda c: (c.x0 + c.x1) / 2)
    found_px = [_to_pixels(c, box_px) for c in chosen]

    missing = expected - len(chosen)
    truncated_left, truncated_right = _truncation(box_px, frame_size)
    missing_at_left = _missing_side(missing, truncated_left, truncated_right, problems,
                                     expected, len(chosen))

    if missing_at_left is None:
        # Либо недостачи нет, либо её причина не установлена. Во втором случае
        # строим разметку из найденного и НЕ объявляем недостающее «за кадром»:
        # замечание уже записано, разметка не пройдёт арифметику — и хорошо.
        ordered: list[tuple[float, float, float, float] | None] = list(found_px)
    elif missing_at_left:
        ordered = [None] * missing + list(found_px)
    else:
        ordered = list(found_px) + [None] * missing

    return _layout(visit_key, camera, size, orientation, ordered,
                    reverse=orientation == NOSE_RIGHT, video=video,
                    frame_idx=frame_idx, frame_size=frame_size, body_px=box_px), problems


def _layout(visit_key, camera, size, orientation, ordered, reverse: bool,
             video: str, frame_idx: int, frame_size, body_px):
    """Складывает записи дверей, нумеруя их от носа.

    `ordered` идёт слева направо по кадру. При носе справа нумерация от носа
    обратна порядку по кадру, поэтому список разворачивается.
    """
    boxes = list(reversed(ordered)) if reverse else ordered
    doors = [
        DoorLayoutEntry(n_from_nose=n, opening_px=box, in_frame=box is not None)
        for n, box in enumerate(boxes, start=1)
    ]
    return DoorLayout(visit_key=visit_key, camera=camera, size=size,
                       orientation=orientation, video=video, frame_idx=frame_idx,
                       frame_size=frame_size, body_px=body_px, doors=doors)


def _to_pixels(c: DoorCandidate,
                box: tuple[float, float, float, float]) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    return (x0 + c.x0 * w, y0 + c.y0 * h, x0 + c.x1 * w, y0 + c.y1 * h)


def _truncation(box: tuple[float, float, float, float],
                 frame_size: tuple[int, int]) -> tuple[bool, bool]:
    width, _height = frame_size
    return box[0] <= EDGE_TOUCH_PX, box[2] >= width - EDGE_TOUCH_PX


def _missing_side(missing: int, truncated_left: bool, truncated_right: bool,
                   problems: list[str], expected: int, found: int) -> bool | None:
    """С какой стороны недостающие двери. None — причина не установлена.

    Сторона нужна не сама по себе, а чтобы назначить недостающим дверям номера
    от носа. Когда обрезаны оба края или не обрезан ни один, гадать нельзя.
    """
    if missing <= 0:
        return False
    if truncated_left and not truncated_right:
        return True
    if truncated_right and not truncated_left:
        return False
    if truncated_left and truncated_right:
        problems.append(
            f"найдено {found} дверей из {expected}, корпус обрезан обоими краями "
            "кадра — с какой стороны недостающие, по геометрии не определить"
        )
        return None
    problems.append(
        f"найдено {found} дверей из {expected}, но корпус ТС виден целиком: "
        "дверь не найдена методом, а не отсутствует в кадре — за кадром её "
        "объявлять нельзя, нужна ручная разметка"
    )
    return None
