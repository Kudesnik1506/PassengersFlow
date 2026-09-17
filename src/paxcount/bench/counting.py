"""Счёт людей по дверям, которые нашёл метод: ответ модели → числа в отчёте.

Стенд `bench_doors` меряет двери в пикселях. Но в отчёт заказчику уходят не
пиксели, а «вошло» и «вышло», и вопрос «какой метод брать» честно решается
только в этих единицах: промах в 40 px при запасе кропа в 140 px не стоит
ничего, а промах на соседнюю дверь стоит всего счёта по визиту.

Модуль ничего не считает сам: счётчик — боевой (`zonecount.count_zone`), тот
же, что в `backends/custom.py`. Здесь только превращение ответа метода в то,
что счётчик умеет принимать, и три места, где это превращение может соврать:

* **отбор.** Кандидатов у модели бывают сотни; сколько из них дверей, пиксели
  не знают — знает таблица 3 по размеру ТС. Отбор идёт готовым арбитром
  (`doorprop.layout.layout_from_candidates`), тем же, что в бою;
* **зона.** Считается полоса ног, а не проём (запрет 4). По горизонтали её
  границы берутся у проёма, по вертикали — у кузова;
* **окно.** Дверная зона без машины — это кусок тротуара. Окно стоянки берётся
  по совпадению рамки ТС с размеченным кузовом, а не назначается вокруг
  размеченного кадра на глаз.
"""

from __future__ import annotations

import numpy as np

from ..core.trackdata import TrackData
from ..core.types import Direction, DoorSpec, VehicleVisit, VideoConfig
from ..doorprop import DoorCandidate
from ..doorprop.layout import layout_from_candidates
# Импорт, а не копия (запрет 9): тем же порогом отличается «та же машина на том
# же месте» при поиске дублей разметки (решение 047) — вопрос здесь тот же.
from ..truth import SAME_VISIT_IOU, DoorLayout, iou
from ..zonecount import activity_window, count_zone
from .cases import Case
from .finders import Box, to_fractions
from .methods.ensemble import consensus_candidates

# Доли высоты кузова, задающие полосу ног. Берутся у `DoorSpec`, а не пишутся
# числом: там они измерены (7116 наблюдений, десятый процентиль точки опоры —
# 0.66 высоты кузова, медиана — 1.12) и там же живёт их обоснование.
_DEFAULT_ZONE = DoorSpec.model_fields["zone"].default
FOOT_BAND = (_DEFAULT_ZONE[1], _DEFAULT_ZONE[3])
# Боковой допуск зоны, долями ВЫСОТЫ ПОЛОСЫ НОГ. Единица измерения выбрана так,
# чтобы допуск сам сжимался вместе с машиной, уехавшей вдаль: полоса — доля
# кузова, а пиксель — нет. Значение выбрано развёрткой по боевым визитам:
# 0 → 15 входов из 22, 0.25 → 18 без ложных, 0.5 → 20 с двумя ложными
# (решение 060). Ложный счёт дороже пропуска — решение 055.
DOOR_SIDE_MARGIN = 0.25


def door_id(n_from_nose: int) -> str:
    return f"д{n_from_nose}"


def door_specs(
    layout: DoorLayout, side_margin: float = DOOR_SIDE_MARGIN,
) -> list[DoorSpec]:
    """Зоны счёта по разметке визита — в абсолютных пикселях кадра.

    Абсолютные, а не доли рамки ТС: машина на стоянке неподвижна, рамка
    канонична на всё окно (запрет 3), и пересчитывать зону по покадровой рамке
    значило бы возвращать в счёт дрожание детектора.

    ``side_margin`` расширяет зону ВБОК на долю высоты полосы ног. Вертикаль он
    не трогает: она измерена по 7116 наблюдениям, а горизонталь — просто
    размеченная ширина проёма, и на боевых записях это бывает 7 пикселей.
    """
    if layout.body_px is None:
        return []
    top = layout.body_px[1] + FOOT_BAND[0] * (layout.body_px[3] - layout.body_px[1])
    bottom = layout.body_px[1] + FOOT_BAND[1] * (layout.body_px[3] - layout.body_px[1])
    margin = side_margin * (bottom - top)
    specs = []
    for door in layout.doors:
        if not door.in_frame or door.opening_px is None:
            continue
        left = door.opening_px[0] - margin
        right = door.opening_px[2] + margin
        specs.append(DoorSpec(
            door_id=door_id(door.n_from_nose), mode="zone", frame="absolute",
            line_start={"x": left, "y": top},
            line_end={"x": right, "y": bottom},
            zone=(left, top, right, bottom),
        ))
    return specs


def layout_from_boxes(
    boxes: list[Box], scores: list[float], case: Case,
) -> tuple[DoorLayout | None, list[str]]:
    """Ответ метода → разметка визита: K дверей по таблице 3, от носа.

    Это и есть та часть метода, которой в `bench_doors` не было: там метод
    сравнивался облаком кандидатов, а считать можно только по дверям.
    """
    weights = scores if len(scores) == len(boxes) else [0.0] * len(boxes)
    candidates = [
        DoorCandidate(*to_fractions(box, case.body_px), score=float(weight),
                       method="bench")
        for box, weight in zip(boxes, weights)
    ]
    return layout_from_candidates(
        candidates, case.size, case.visit_key, case.camera, case.orientation,
        case.body_px, case.frame_size, case.video, case.frame_idx,
    )


def council_layout(
    answers: dict[str, list[Box]], case: Case,
) -> tuple[DoorLayout | None, list[str]]:
    """Совет методов: голосуют их ОТВЕТЫ, а не сырые облака кандидатов.

    В стенде совет собирался из необработанных предложений, и метод, выдающий
    девятьсот рамок на кадр, голосовал за каждое место сразу — вес «сколько
    методов согласны» при таком участнике не значит ничего. Поэтому на вход
    сюда идут уже отобранные ответы: по K дверей от каждого метода.
    """
    candidates = consensus_candidates(answers, case.body_px)
    return layout_from_candidates(
        candidates, case.size, case.visit_key, case.camera, case.orientation,
        case.body_px, case.frame_size, case.video, case.frame_idx,
    )


def stop_window(
    data: TrackData,
    body_px: tuple[float, float, float, float],
    iou_min: float = SAME_VISIT_IOU,
) -> tuple[float, float] | None:
    """Окно, в котором рамка ТС совпадает с размеченным кузовом.

    `None` — совпадения не нашлось: машину не видно или детектор её не взял.
    Тогда окна нет, а не «возьмём весь отрезок»: счёт по дверной зоне без
    машины — это счёт прохожих.
    """
    stamps = [
        f.ts for f in data.frames
        if any(iou(tuple(float(v) for v in box), body_px) >= iou_min
                for box in f.vehicle_boxes)
    ]
    return (min(stamps), max(stamps)) if stamps else None


def count_doors(
    data: TrackData,
    layout: DoorLayout,
    window: tuple[float, float],
    side_margin: float = DOOR_SIDE_MARGIN,
) -> dict[str, tuple[int, int]]:
    """Вошло и вышло по каждой двери разметки. Счётчик — боевой, не свой."""
    specs = door_specs(layout, side_margin)
    counts = {spec.door_id: (0, 0) for spec in specs}
    if not specs:
        return counts

    # Рамка даётся на ВСЕ кадры отрезка, а не только на окно стоянки: машина
    # стоит, рамка каноническая, и по этим кадрам счётчик восстанавливает
    # историю трека. Окно ограничивает событие, а не историю (`count_zone`).
    boxes_by_frame = {
        f.frame_idx: np.asarray(layout.body_px, dtype=float) for f in data.frames
    }
    visit = VehicleVisit(video=data.video, visit_id=1, vehicle_track_id=-1,
                          arrival_ts=window[0], departure_ts=window[1])
    # Сужение окна до фактической активности у дверей — тот же шаг, что в
    # боевом бэкенде: чем шире окно, тем больше случайных прохожих.
    lo, hi = activity_window(data, specs, visit, boxes_by_frame)
    visit = visit.model_copy(update={"arrival_ts": lo, "departure_ts": hi})

    events = count_zone(data, VideoConfig(video=data.video), [visit],
                         {1: boxes_by_frame}, {1: specs})
    for event in events:
        inn, out = counts.get(event.door_id, (0, 0))
        counts[event.door_id] = (
            (inn + 1, out) if event.direction is Direction.IN else (inn, out + 1)
        )
    return counts


# Три модели и их совет: те же имена в обоих стендах — счёт классикой и счёт
# моделью обязаны идти по одним и тем же дверям, иначе сравниваются не
# счётчики, а две разные разметки.
MODELS = ("groundingdino", "vlpart", "owlv2")
VARIANTS = ("эталон-двери", *MODELS, "совет")

# Запас детекции вокруг размеченного кадра. Стоянка замерена в 11-20 с, а
# размеченный кадр может стоять у любого её края — отсюда запас в обе стороны.
# Это НЕ окно счёта: счёт идёт по окну, где рамка ТС совпала с размеченным
# кузовом (`stop_window`). Величина общая для обоих стендов: счёт классикой и
# счёт моделью обязаны смотреть на один и тот же отрезок записи.
DETECT_WINDOW_S = 25.0


def variants_for(visit_key: str, case: Case, truth_layout: DoorLayout,
                  runs) -> dict[str, DoorLayout | None]:
    """Пять наборов дверей на один визит: потолок, три модели и их совет.

    Потолок обязателен в любом сравнении способов поиска дверей (решение 050):
    без него неизвестно, упирается ли счёт в двери вообще. `None` означает «у
    варианта разметки нет» и НЕ означает «дверей ноль»: вариант без разметки в
    знаменатель метрики не идёт.
    """
    from .run import OK

    out: dict[str, DoorLayout | None] = {"эталон-двери": truth_layout}
    answers: dict[str, list] = {}
    for method in MODELS:
        run = next((r for r in runs if r.method == method
                     and r.visit_key == visit_key and r.status == OK), None)
        if run is None:
            out[method] = None
            continue
        layout, _ = layout_from_boxes(list(run.predicted), list(run.scores), case)
        out[method] = layout
        if layout is not None:
            answers[method] = [d.opening_px for d in layout.doors
                                if d.opening_px is not None]
    out["совет"] = council_layout(answers, case)[0] if len(answers) >= 2 else None
    return out
