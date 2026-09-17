"""Счёт по зоне двери: появление и исчезновение треков.

Для наружной камеры пересечения линии не происходит: вошедший человек просто
пропадает за корпусом, а вышедший возникает из ниоткуда в дверном проёме.
Поэтому событием считается не пересечение, а рождение и смерть трека внутри
дверной зоны, привязанной к bbox транспортного средства.

Ложные срабатывания отсекаются тремя условиями: трек должен прожить минимум
``min_seconds``, не обрываться на краю кадра (там человек просто вышел из
поля зрения) и не обрываться вместе с концом визита.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .core.geometry import distance_to_segment
from .core.trackdata import TrackData
from .core.types import Direction, DoorSpec, PersonEvent, VehicleVisit, VideoConfig

MIN_TRACK_SECONDS = 0.5
BORDER_MARGIN = 12  # пикселей от края кадра
EDGE_GUARD_SECONDS = 0.6  # у самых границ визита события не засчитываем
EDGE_GUARD_VISIT_SHARE = 0.15  # но не больше этой доли самого визита
# Сколько точек трека смотрим, оценивая направление ухода от двери.
DIRECTION_POINTS = 8
# Минимальное смещение, ниже которого направление считаем неопределённым (в
# долях высоты bbox человека — так порог не зависит от масштаба сцены).
# Было 0.25: на кадрах, где человек крупный (близко к камере, высота бокса
# ~150-190 px), это требует нереального шага за DIRECTION_POINTS кадров и
# отклоняет реальные проходы с «почти не сдвинулся». Замер на 04 в
# гипотетическом zone-режиме (реальный конфиг там — line, см. ниже) показал
# плато 0.10-0.15: ниже 0.10 начинаются ложные срабатывания на 03.
MIN_SHIFT_RATIO = 0.15
# Насколько вертикальная составляющая должна преобладать над горизонтальной.
VERTICAL_RATIO = 0.35
# Порог «стоял на месте» для запасного признака `_by_displacement`, в долях
# роста человека. Отдельная величина от MIN_SHIFT_RATIO намеренно: тот меряет
# смещение за DIRECTION_POINTS кадров (0.8 с), а этот — за ВСЮ жизнь трека, и
# одно число на два разных отрезка означает разное. Проверено ценой: подставив
# сюда 0.15, теряем вход на визите 5.
# Развёртка по боевым визитам: плато 0.01-0.06 (входы 18, выходы 3, ложных
# нет), при 0.07 визит 3 теряет свой выход. Взято самое строгое внутри плато.
STILL_RATIO = 0.05
# Подмена ID: если рядом с местом смерти трека почти сразу рождается другой,
# это не проход через дверь, а перекрытие. Окно по времени и радиус в долях
# роста человека.
SWITCH_SECONDS = 0.3
SWITCH_RADIUS = 0.5
# Сколько кадров трек должен провести в дверной зоне, чтобы считаться прошедшим
# через неё. Один кадр — это дрожание рамки на границе, а не проход.
MIN_ZONE_FRAMES = 2


@dataclass
class TrackLife:
    track_id: int
    first_ts: float = 0.0
    last_ts: float = 0.0
    first_frame: int = 0
    last_frame: int = 0
    first_box: np.ndarray | None = None
    last_box: np.ndarray | None = None
    first_in_zone: bool = False
    last_in_zone: bool = False
    # Последний кадр трек провёл ВЫШЕ полосы ног и внутри рамки кузова — то
    # есть в салоне. Признак считается там, где известна геометрия (в
    # `collect_lives`), а не в `classify`: правило не должно знать про пиксели.
    last_above_zone: bool = False
    frames: int = 0
    in_zone_frames: int = 0
    anchors: list[tuple[float, float]] = field(default_factory=list)
    # Сколько кадров трек провёл в зоне каждой двери: по этому счётчику
    # событие приписывается конкретной двери, когда дверей несколько.
    door_frames: dict[str, int] = field(default_factory=dict)
    # Первый и последний кадр в зоне каждой двери — вход атрибутируем по концу
    # трека, выход по началу, иначе прошедший вдоль борта человек припишется
    # той двери, мимо которой шёл, а не той, в которой исчез.
    door_first: dict[str, int] = field(default_factory=dict)
    door_last: dict[str, int] = field(default_factory=dict)
    # Когда человек был у двери в первый и последний раз. Этим и датируется
    # событие: оно происходит у двери, а не тогда, когда детектор потерял трек.
    # Вошедшего на К2 видно сквозь стекло ещё полминуты после посадки.
    first_zone_ts: float | None = None
    last_zone_ts: float | None = None
    first_zone_frame: int | None = None
    last_zone_frame: int | None = None


def _anchor(box: np.ndarray) -> tuple[float, float]:
    return float((box[0] + box[2]) / 2), float(box[3])


def _inside(anchor: tuple[float, float], zone: tuple[float, float, float, float]) -> bool:
    x0, y0, x1, y1 = zone
    return x0 <= anchor[0] <= x1 and y0 <= anchor[1] <= y1


def _touches_border(box: np.ndarray, width: int, height: int) -> bool:
    return bool(
        box[0] <= BORDER_MARGIN
        or box[1] <= BORDER_MARGIN
        or box[2] >= width - BORDER_MARGIN
        or box[3] >= height - BORDER_MARGIN
    )


def collect_lives(
    data: TrackData,
    specs: list[DoorSpec],
    visit: VehicleVisit,
    boxes_by_frame: dict[int, np.ndarray],
) -> dict[int, TrackLife]:
    """Один проход по кадрам сразу по всем дверям визита.

    Раньше проход делался заново на каждую дверь, и события копились по
    каждой независимо: при одной двери это не проявлялось, а при двух
    перекрывающихся зонах один человек давал два события. Здесь «в зоне»
    считается по **объединению** зон, а принадлежность конкретной двери
    сохраняется отдельно, в ``door_frames``.

    Границы визита здесь НЕ применяются: жизнь трека собирается по всем
    кадрам, для которых известна рамка ТС. Окно ограничивает событие, а не
    историю — этим занимается ``count_zone``. Обрезка истории границей уже
    стоила счёта: на боевых визитах 4 и 5 несколько треков начинались ровно на
    границе окна, потому что окно открывается, когда машина УЖЕ встала, а
    пассажиры к этому моменту стоят у двери. Их «рождение» вычислялось по
    обрубку, и подошедший снаружи выглядел как возникший в проёме.

    Поведение боевого бэкенда от этого не меняется: `visits.visit_boxes` и так
    отдаёт рамки только внутри визита, то есть шире набор не становится.
    """
    lives: dict[int, TrackLife] = {}
    zone_specs = [s for s in specs if s.mode == "zone"]
    for f in data.frames:
        bbox = boxes_by_frame.get(f.frame_idx)
        if bbox is None:
            continue
        box_tuple = tuple(float(v) for v in bbox)
        zones = [(s.door_id, s.resolve_zone(box_tuple)) for s in zone_specs]
        for tid, pbox in zip(f.person_ids, f.person_boxes):
            tid = int(tid)
            anchor = _anchor(pbox)
            hits = [door_id for door_id, zone in zones if _inside(anchor, zone)]
            in_zone = bool(hits)
            # Порог салона спрашивается у СВОЕЙ двери — той, над чьей полосой
            # человек стоит. Общий порог по всем дверям (раньше здесь был
            # `min`) верен, только пока полосы всех дверей на одной высоте. На
            # угловом ракурсе пороги дверей расходятся по кадру на сотни
            # пикселей (решение 062), и общий порог объявил бы салоном узкую
            # щель под крышей: вошедший в низкую дверь потерялся бы из-за
            # двери, у которой он не стоял.
            zone_top = _own_zone_top(anchor, zones, box_tuple)
            above = (not in_zone
                      and box_tuple[0] <= anchor[0] <= box_tuple[2]
                      and box_tuple[1] <= anchor[1] < zone_top)
            life = lives.get(tid)
            if life is None:
                life = TrackLife(
                    track_id=tid, first_ts=f.ts, first_frame=f.frame_idx,
                    first_box=pbox, first_in_zone=in_zone,
                )
                lives[tid] = life
            life.last_ts = f.ts
            life.last_frame = f.frame_idx
            life.last_box = pbox
            life.last_in_zone = in_zone
            life.last_above_zone = above
            life.frames += 1
            life.in_zone_frames += in_zone
            if in_zone:
                if life.first_zone_ts is None:
                    life.first_zone_ts = f.ts
                    life.first_zone_frame = f.frame_idx
                life.last_zone_ts = f.ts
                life.last_zone_frame = f.frame_idx
            life.anchors.append(anchor)
            for door_id in hits:
                life.door_frames[door_id] = life.door_frames.get(door_id, 0) + 1
                life.door_first.setdefault(door_id, f.frame_idx)
                life.door_last[door_id] = f.frame_idx
    return lives


def attribute_door(life: TrackLife, direction: Direction, fallback: str) -> str:
    """Какой двери приписать событие трека.

    Вход засчитывается по исчезновению, поэтому смотрим, в чьей зоне трек
    оказался в конце; выход — по появлению, значит смотрим начало. Тай-брейк
    по числу кадров в зоне, затем по имени двери — чтобы результат не зависел
    от порядка обхода словаря.
    """
    if not life.door_frames:
        return fallback
    if direction is Direction.IN:
        key = life.door_last
        best_moment = max(key.values())
    else:
        key = life.door_first
        best_moment = min(key.values())
    near = [d for d, moment in key.items() if moment == best_moment]
    return max(near, key=lambda d: (life.door_frames.get(d, 0), d))


def _leaves_vehicle(life: TrackLife) -> tuple[bool, str]:
    """Уходит ли человек от корпуса на камеру сразу после появления.

    Вышедший из ТС делает шаг наружу: его точка опоры уезжает вниз кадра.
    Прохожий, у которого трек порвался из-за перекрытия, движется вдоль
    корпуса — почти горизонтально. Это и есть разделяющий признак.

    Ослабление до «удаления от проёма в любую сторону» пробовали и отменили
    (решение 055): на боевом наборе оно дало один настоящий выход и тут же
    ложный там, где эталон говорит ноль, потому что прохожий вдоль борта и
    вышедший вдоль борта геометрически совпадают. Возвращать ослабление без
    нового разделяющего признака бесполезно. Цена, которую мы платим здесь, —
    выход вдоль борта не засчитывается.
    """
    return _shift_is_outward(life.anchors[:DIRECTION_POINTS], life.first_box, +1)


def _enters_vehicle(life: TrackLife) -> tuple[bool, str]:
    """Идёт ли человек к корпусу перед тем, как пропасть."""
    return _shift_is_outward(life.anchors[-DIRECTION_POINTS:], life.last_box, -1)


def _rose_into_cabin(life: TrackLife) -> tuple[bool, str]:
    """Пришёл ли человек в салон снизу — от двери, а не откуда-то сверху.

    Мера та же, что у остальных признаков направления: смещение в долях роста
    человека. Считается от начала трека до конца, а не по последним точкам:
    вошедший останавливается в салоне, и на последних кадрах он неподвижен —
    по ним его проход неотличим от стояния на месте.
    """
    if len(life.anchors) < 3 or life.last_box is None:
        return False, "мало точек"
    shift = np.asarray(life.anchors[-1], dtype=float) - np.asarray(
        life.anchors[0], dtype=float)
    person_h = max(float(life.last_box[3] - life.last_box[1]), 1.0)
    if -shift[1] < MIN_SHIFT_RATIO * person_h:
        return False, "поднялся недостаточно"
    return True, "ок"


def _shift_is_outward(points, box, sign: int) -> tuple[bool, str]:
    if len(points) < 3 or box is None:
        return False, "мало точек"
    arr = np.asarray(points, dtype=float)
    shift = arr[-1] - arr[0]
    person_h = max(float(box[3] - box[1]), 1.0)
    if np.hypot(*shift) < MIN_SHIFT_RATIO * person_h:
        return False, "почти не сдвинулся"
    if sign * shift[1] <= 0:
        return False, "движется не в ту сторону"
    if abs(shift[1]) < VERTICAL_RATIO * abs(shift[0]):
        return False, "движется вдоль корпуса"
    return True, "ок"


ACTIVITY_GUARD_SECONDS = 1.0  # запас по краям найденной активности


def activity_window(
    data: TrackData,
    specs: list[DoorSpec],
    visit: VehicleVisit,
    boxes_by_frame: dict[int, np.ndarray],
    guard: float = ACTIVITY_GUARD_SECONDS,
) -> tuple[float, float]:
    """Сужает окно счёта визита до фактической активности у дверей.

    Визит — окно, где ТС стоит целиком; настоящие события занимают меньший
    интервал внутри него (на 04 замерено: визит 43 с, события — 7 с). Чем
    шире окно счёта, тем больше шансов, что случайный прохожий в кадре
    попадёт под классификацию. Это защита точности, а не рычаг полноты: если
    активности не нашлось, возвращается исходное окно визита без изменений.
    """
    lo = hi = None
    for f in data.frames:
        if not (visit.arrival_ts <= f.ts <= visit.departure_ts):
            continue
        bbox = boxes_by_frame.get(f.frame_idx)
        if bbox is None or len(f.person_boxes) == 0:
            continue
        box_tuple = tuple(float(v) for v in bbox)
        near = False
        for spec in specs:
            if spec.mode == "zone":
                zone = spec.resolve_zone(box_tuple)
                near = any(_inside(_anchor(pb), zone) for pb in f.person_boxes)
            else:
                a, b = spec.resolve(box_tuple)
                height = float(box_tuple[3] - box_tuple[1])
                max_dist = max(spec.band * height, 1.0)
                near = any(
                    distance_to_segment(_anchor(pb), a, b) <= max_dist
                    for pb in f.person_boxes
                )
            if near:
                break
        if near:
            lo = f.ts if lo is None else lo
            hi = f.ts

    if lo is None:
        return visit.arrival_ts, visit.departure_ts
    return max(visit.arrival_ts, lo - guard), min(visit.departure_ts, hi + guard)


def edge_guard_seconds(visit: VehicleVisit) -> float:
    """Защитная зона у границ визита, но не больше доли самого визита.

    Фиксированные 0.6 с съедали треть короткого визита: автобус на остановке
    стоит секунды, и настоящая высадка в первые полсекунды после остановки —
    норма, а не артефакт. Для длинного визита порог остаётся прежним.
    """
    duration = max(visit.departure_ts - visit.arrival_ts, 0.0)
    return min(EDGE_GUARD_SECONDS, EDGE_GUARD_VISIT_SHARE * duration)


def classify(
    life: TrackLife,
    visit: VehicleVisit,
    width: int,
    height: int,
    min_seconds: float,
    edge_guard: float | None = None,
) -> tuple[Direction | None, str]:
    """Возвращает (событие, причина). Причина нужна для диагностики."""
    if life.last_ts - life.first_ts < min_seconds:
        return None, "короткий трек"

    guard = edge_guard_seconds(visit) if edge_guard is None else edge_guard

    born_inside = life.first_in_zone and not _touches_border(life.first_box, width, height)
    died_inside = life.last_in_zone and not _touches_border(life.last_box, width, height)

    # Запас считается только ВНУТРЬ визита. Отрицательный остаток значит, что
    # трек начался раньше визита или пережил его, — это полная история, а не
    # обрыв на границе (решение 052), и глушить по ней событие нельзя.
    born_at_start = 0.0 <= life.first_ts - visit.arrival_ts < guard
    died_at_end = 0.0 <= visit.departure_ts - life.last_ts < guard

    if died_inside and not died_at_end and not born_inside:
        ok, why = _enters_vehicle(life)
        if ok:
            return Direction.IN, "исчез в зоне двери"
        # Тот же запасной критерий, что и в ветке «родился и умер в зоне»
        # ниже. Без него решение зависело от того, родился трек на пару
        # пикселей внутри зоны или снаружи: на ролике 09 пассажир с коляской
        # в одной версии записи попадал в ветку «топтался у двери» и
        # засчитывался, а в другой (трек начался чуть раньше, снаружи зоны) —
        # отклонялся, хотя событие физически то же самое.
        direction, reason = _by_displacement(life)
        if direction is Direction.IN:
            return direction, f"исчез в зоне двери ({reason})"
        return None, f"вход отклонён: {why}"
    if born_inside and not born_at_start and not died_inside:
        # Развилка проверяется до выхода: поднявшийся в салон — вход
        # (решение 053), а не «не событие» из-за того, что шёл не вниз кадра.
        if life.last_above_zone:
            ok, why = _rose_into_cabin(life)
            if ok:
                return Direction.IN, "ушёл из зоны двери в салон"
            return None, f"вход отклонён: {why}"
        ok, why = _leaves_vehicle(life)
        return (Direction.OUT, "появился в зоне двери") if ok else (
            None, f"выход отклонён: {why}"
        )
    if born_inside and died_inside and not born_at_start and not died_at_end:
        # И родился, и умер в зоне: человек топтался у двери. Решаем по
        # смещению — ушёл вглубь зоны (вошёл) или наружу (вышел).
        out_ok, _ = _leaves_vehicle(life)
        in_ok, _ = _enters_vehicle(life)
        if out_ok and not in_ok:
            return Direction.OUT, "вышел и остался у двери"
        if in_ok and not out_ok:
            return Direction.IN, "подошёл к двери и пропал"
        return _by_displacement(life)
    if (life.last_above_zone and life.in_zone_frames >= MIN_ZONE_FRAMES
            and not born_inside and not died_at_end):
        # Вошёл, но не пропал: на К2 автобус во весь кадр, и пассажира видно
        # сквозь стекло уже в салоне. Исчезновение за корпусом — частный
        # случай входа, а не его определение.
        ok, why = _rose_into_cabin(life)
        if ok:
            return Direction.IN, "ушёл из зоны двери в салон"
        return None, f"вход отклонён: {why}"
    return None, "нет события"


def _own_zone_top(anchor, zones, body) -> float:
    """Верх полосы ног той двери, у которой человек стоит.

    «Своя» дверь — та, чей горизонтальный створ накрывает точку опоры; если ни
    одна не накрывает, берётся ближайшая по горизонтали. Без дверей порогом
    салона служит низ кузова: тогда «выше полосы» вырождается в «внутри рамки».
    """
    if not zones:
        return body[3]
    covering = [zone[1] for _, zone in zones if zone[0] <= anchor[0] <= zone[2]]
    if covering:
        return max(covering)
    return min(
        zones, key=lambda z: abs((z[1][0] + z[1][2]) / 2 - anchor[0])
    )[1][1]


def _by_displacement(life: TrackLife) -> tuple[Direction | None, str]:
    """Запасной критерий: куда человек сместился за всю жизнь трека.

    Порог смещения — доля роста человека, а не пиксели. Абсолютные 20 px,
    стоявшие здесь раньше, означали на разных записях разное: на визите 3
    автобус снят с торца, человек в кадре под сотню пикселей, и 20 px — пятая
    часть его роста; на визите 1 человек 370 px, и те же 20 px — одна
    двадцатая. Порог тем самым задавался масштабом сцены, а не поведением.
    Величина своя (`STILL_RATIO`), а не заимствованная у прямых признаков: те
    меряют смещение за 0.8 секунды, этот — за всю жизнь трека.
    """
    if len(life.anchors) < 4:
        return None, "мало точек"
    box = life.last_box if life.last_box is not None else life.first_box
    if box is None:
        return None, "мало точек"
    start = np.mean(life.anchors[: max(2, len(life.anchors) // 5)], axis=0)
    end = np.mean(life.anchors[-max(2, len(life.anchors) // 5) :], axis=0)
    shift = end - start
    person_h = max(float(box[3] - box[1]), 1.0)
    if np.hypot(*shift) < STILL_RATIO * person_h:
        return None, "стоял на месте"
    return (Direction.IN, "ушёл вглубь зоны") if shift[1] < 0 else (
        Direction.OUT, "вышел из зоны",
    )


def _is_id_switch(life: TrackLife, others: list[TrackLife], moment: str) -> bool:
    """Рядом с обрывом трека родился (или умер) другой — значит, это перекрытие."""
    if moment == "death":
        ts, anchor, box = life.last_ts, life.anchors[-1], life.last_box
    else:
        ts, anchor, box = life.first_ts, life.anchors[0], life.first_box
    if box is None:
        return False
    radius = SWITCH_RADIUS * max(float(box[3] - box[1]), 1.0)
    for other in others:
        if other.track_id == life.track_id:
            continue
        # Смерть трека объясняется рождением соседнего, и наоборот.
        other_ts = other.first_ts if moment == "death" else other.last_ts
        other_anchor = other.anchors[0] if moment == "death" else other.anchors[-1]
        if abs(other_ts - ts) > SWITCH_SECONDS:
            continue
        if np.hypot(other_anchor[0] - anchor[0], other_anchor[1] - anchor[1]) <= radius:
            return True
    return False


def event_moment(life: TrackLife, direction: Direction) -> tuple[float, int]:
    """Когда и на каком кадре произошло событие этого трека.

    Событие происходит У ДВЕРИ, а не тогда, когда детектор нашёл или потерял
    человека: вошедшего на К2 видно сквозь стекло ещё полминуты после посадки,
    и датировать вход смертью трека значило бы вынести событие за окно визита
    вместе с самим событием. Момент один и тот же для счёта и для разбора —
    поэтому он живёт здесь, а не в каждом из них своей копией.
    """
    if direction is Direction.IN:
        if life.last_zone_ts is not None and life.last_zone_frame is not None:
            return life.last_zone_ts, life.last_zone_frame
        return life.last_ts, life.last_frame
    if life.first_zone_ts is not None and life.first_zone_frame is not None:
        return life.first_zone_ts, life.first_zone_frame
    return life.first_ts, life.first_frame


def count_zone(
    data: TrackData,
    config: VideoConfig,
    visits: list[VehicleVisit],
    boxes_by_visit: dict[int, dict[int, np.ndarray]],
    specs_by_visit: dict[int, list[DoorSpec]] | None = None,
) -> list[PersonEvent]:
    """События по зонам дверей — по одному на трек и направление за визит.

    Два разных человека, вошедшие одновременно в две двери, дают два события:
    дедупликация идёт по треку, а не по визиту. А вот один человек в зоне
    перекрытия двух дверей даёт ровно одно событие — дверь выбирает
    ``attribute_door``.

    Рамки ТС приходят СВОИ на каждый визит: на одном ролике визитов может быть
    много и от разных машин, в том числе одновременных.
    """
    events: list[PersonEvent] = []
    min_seconds = MIN_TRACK_SECONDS

    for visit in visits:
        specs = (specs_by_visit or {}).get(visit.visit_id, config.doors)
        zone_specs = [s for s in specs if s.mode == "zone"]
        if not zone_specs:
            continue
        fallback_door = zone_specs[0].door_id
        lives = collect_lives(data, zone_specs, visit, boxes_by_visit[visit.visit_id])
        all_lives = list(lives.values())
        edge_guard = edge_guard_seconds(visit)
        for life in all_lives:
            direction, _ = classify(
                life, visit, data.width, data.height, min_seconds, edge_guard
            )
            if direction is None:
                continue
            moment = "death" if direction is Direction.IN else "birth"
            if _is_id_switch(life, all_lives, moment):
                continue
            ts, frame_idx = event_moment(life, direction)
            # История трека берётся целиком, но событие обязано попасть в окно
            # визита: вошедший в предыдущую машину не наш пассажир.
            if not (visit.arrival_ts <= ts <= visit.departure_ts):
                continue
            events.append(
                PersonEvent(
                    video=data.video, visit_id=visit.visit_id, event_ts=round(ts, 2),
                    frame_idx=frame_idx, person_track_id=life.track_id,
                    door_id=attribute_door(life, direction, fallback_door),
                    direction=direction, confidence=0.8,
                )
            )
    events.sort(key=lambda e: (e.event_ts, e.person_track_id))
    return events
