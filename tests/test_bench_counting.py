"""Счёт людей по дверям, найденным разными методами: превращение ответа в зоны.

Между «метод назвал прямоугольники» и «посчитали людей» лежат три шага, и
каждый может тихо испортить счёт:

* **отбор.** Модель отдаёт облако кандидатов (у OWLv2 их под тысячу на кадр).
  Считать по каждому нельзя — нужен отбор по числу дверей из таблицы 3, а
  чтобы отбор был осмысленным, кандидаты должны идти по убыванию уверенности
  САМОЙ модели, а не в том порядке, в каком их вернула библиотека;
* **зона.** Считается не проём, а полоса ног (запрет 4): проём ног не
  накрывает, а зона, поднятая выше, ловит стоящих в салоне;
* **окно.** Пока машины у остановки нет, дверная зона — это кусок тротуара, и
  каждый прохожий в ней станет пассажиром. Окно берётся не на глаз, а по тому,
  когда рамка ТС совпала с размеченным кузовом.
"""

from __future__ import annotations

import numpy as np
import pytest
from paxcount.bench.counting import (
    DOOR_SIDE_MARGIN, count_doors, door_specs, layout_from_boxes, stop_window,
)
from paxcount.bench.cases import Case
from paxcount.bench.finders import ranked
from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.core.types import Direction, DoorSpec, VehicleVisit
from paxcount.delivery.model import VehicleSize
from paxcount.truth import DoorLayout, DoorLayoutEntry

BODY = (100.0, 300.0, 900.0, 800.0)
VIDEO = "2026-09-10 - 06-56-06 - 22739_2 - 02"


# ---- Порядок кандидатов ------------------------------------------------------


def test_candidates_come_back_by_the_models_own_confidence():
    """Отбор «первые K» имеет смысл, только если первые — самые уверенные."""
    boxes = [(0.0, 0.0, 1.0, 1.0), (2.0, 0.0, 3.0, 1.0), (4.0, 0.0, 5.0, 1.0)]
    order, scores = ranked(boxes, [0.2, 0.9, 0.5])
    assert scores == [0.9, 0.5, 0.2]
    assert order[0] == (2.0, 0.0, 3.0, 1.0)


def test_ranking_without_scores_keeps_the_original_order():
    """Метод, не сообщающий уверенности, не должен молча перемешиваться."""
    boxes = [(0.0, 0.0, 1.0, 1.0), (2.0, 0.0, 3.0, 1.0)]
    order, scores = ranked(boxes, [])
    assert order == boxes and scores == [0.0, 0.0]


# ---- Зона двери --------------------------------------------------------------


def layout(openings) -> DoorLayout:
    return DoorLayout(
        visit_key="2/2026-09-10T06:58:51/—", camera="2", size=VehicleSize.LARGE,
        orientation="нос-справа", video=VIDEO, frame_idx=4956,
        frame_size=(1920, 1080), body_px=BODY,
        doors=[DoorLayoutEntry(n_from_nose=n, opening_px=box, in_frame=box is not None)
                for n, box in enumerate(openings, start=1)],
    )


def test_zone_is_the_foot_band_of_the_body_not_the_opening():
    """Запрет 4: считаем полосу ног. По горизонтали — проём, по вертикали — кузов."""
    spec = door_specs(layout([(500.0, 380.0, 600.0, 700.0)] * 3), side_margin=0.0)[0]
    band = DoorSpec.model_fields["zone"].default  # оттуда и берётся полоса ног
    height = BODY[3] - BODY[1]
    assert spec.frame == "absolute" and spec.mode == "zone"
    assert spec.zone == pytest.approx(
        (500.0, BODY[1] + band[1] * height, 600.0, BODY[1] + band[3] * height)
    )


def test_door_out_of_frame_gets_no_zone():
    specs = door_specs(layout([(500.0, 380.0, 600.0, 700.0), None, None]))
    assert len(specs) == 1, "у двери за краем кадра зоны нет и быть не может"


# ---- Отбор кандидатов по числу дверей ---------------------------------------


def case_of(size=VehicleSize.LARGE) -> Case:
    return Case(visit_key="2/x/—", camera="2", video=VIDEO, frame_idx=1,
                 frame_size=(1920, 1080), body_px=BODY, size=size,
                 orientation="нос-справа",
                 doors_visible=((500.0, 380.0, 600.0, 700.0),), doors_total=3)


def test_selection_keeps_as_many_doors_as_the_size_table_says():
    """Из облака кандидатов берётся ровно K, и берутся самые уверенные."""
    boxes = [(200.0 + 60 * i, 380.0, 240.0 + 60 * i, 700.0) for i in range(8)]
    scores = [0.1] * 5 + [0.9, 0.8, 0.7]
    result, _ = layout_from_boxes(boxes, scores, case_of())
    kept = [d.opening_px for d in result.doors if d.opening_px is not None]
    assert len(kept) == 3
    assert {round(b[0]) for b in kept} == {500, 560, 620}, "взяты три самых уверенных"


def test_selection_without_candidates_gives_no_layout_and_says_so():
    result, problems = layout_from_boxes([], [], case_of())
    assert result is None or not [d for d in result.doors if d.in_frame]
    assert problems


# ---- Окно стоянки ------------------------------------------------------------


def frame_at(idx: int, fps: float, vehicle=None, people=()) -> FrameTracks:
    return FrameTracks(
        frame_idx=idx, ts=idx / fps,
        person_ids=np.array([p[0] for p in people], dtype=int),
        person_boxes=np.array([p[1] for p in people], dtype=float).reshape(-1, 4),
        person_conf=np.ones(len(people)),
        vehicle_ids=np.array([1] if vehicle is not None else [], dtype=int),
        vehicle_boxes=np.array([vehicle] if vehicle is not None else [],
                                dtype=float).reshape(-1, 4),
        vehicle_names=["bus"] if vehicle is not None else [],
    )


def test_stop_window_starts_when_the_vehicle_matches_the_marked_body():
    far = (1400.0, 300.0, 1900.0, 800.0)
    data = TrackData(video=VIDEO, width=1920, height=1080, fps=10.0, stride=1,
                      frames=[frame_at(i, 10.0, far if i < 3 or i > 6 else BODY)
                               for i in range(10)])
    window = stop_window(data, BODY)
    assert window == pytest.approx((0.3, 0.6))


def test_stop_window_is_none_when_the_vehicle_never_matches():
    far = (1400.0, 300.0, 1900.0, 800.0)
    data = TrackData(video=VIDEO, width=1920, height=1080, fps=10.0, stride=1,
                      frames=[frame_at(i, 10.0, far) for i in range(10)])
    assert stop_window(data, BODY) is None, "окно не выдумывается, когда машины нет"


# ---- Счёт по дверям ----------------------------------------------------------


def test_person_vanishing_in_the_door_zone_is_counted_as_boarding():
    """Сквозная проверка: человек подходит к двери снизу и пропадает в ней."""
    fps = 10.0
    frames = []
    for i in range(61):                       # 6 секунд визита
        ts_people = []
        if 10 <= i <= 30:                     # трек живёт с 1.0 до 3.0 с
            y = 900.0 - (i - 10) * 10.0       # точка опоры едет вверх кадра
            ts_people.append((7, (525.0, y - 100.0, 575.0, y)))
        frames.append(frame_at(i, fps, BODY, ts_people))
    data = TrackData(video=VIDEO, width=1920, height=1080, fps=fps, stride=1,
                      frames=frames)

    # Двери РАЗНЫЕ: у трёх одинаковых зон событие достаётся любой из них, и
    # тест проверял бы не счёт, а порядок перебора.
    visit_layout = layout([(500.0, 380.0, 600.0, 700.0),
                            (250.0, 380.0, 350.0, 700.0),
                            (700.0, 380.0, 800.0, 700.0)])
    counts = count_doors(data, visit_layout, (0.0, 6.0))
    assert counts["д1"] == (1, 0), "вошёл один, вышедших нет"
    assert sum(inn for inn, _ in counts.values()) == 1, "и ровно один на все двери"


# ---- Совет: голосуют ответы методов, а не их облака --------------------------


def test_council_prefers_the_place_two_methods_agree_on():
    """Вес — число согласных методов, поэтому одинокий кандидат проигрывает.

    Голосуют именно ОТВЕТЫ (по K дверей от каждого метода), а не сырые облака:
    метод, засыпающий кадр тысячей рамок, иначе проголосовал бы за всё сразу и
    вес перестал бы что-либо значить.
    """
    from paxcount.bench.counting import council_layout

    agreed = (500.0, 380.0, 600.0, 700.0)
    alone = (250.0, 380.0, 350.0, 700.0)
    result, _ = council_layout(
        {"a": [agreed], "b": [(505.0, 380.0, 605.0, 700.0)], "c": [alone]},
        case_of(size=VehicleSize.SMALL),   # у малого ТС одна дверь — арбитр строг
    )
    kept = [d.opening_px for d in result.doors if d.opening_px is not None]
    assert len(kept) == 1
    assert kept[0][0] == pytest.approx(502.5, abs=5), "взято место, где сошлись двое"


# ---- Пять наборов дверей на визит --------------------------------------------


def run_of(method: str, key: str, openings) -> "MethodRun":
    from paxcount.bench.summary import MethodRun
    from paxcount.bench.run import OK
    boxes, scores = [], []
    for opening in openings:
        boxes.append(opening)
        scores.append(0.9)
    return MethodRun(method=method, visit_key=key, status=OK, note="",
                      predicted=tuple(boxes), scores=tuple(scores))


def test_variants_give_the_ceiling_and_every_method_by_name():
    """Разбор на варианты — один на оба стенда, а не копия в каждом.

    Счёт классикой и счёт моделью обязаны сравниваться на ОДНИХ И ТЕХ ЖЕ
    дверях, иначе сравнивается не счётчик, а две разные разметки. Общая
    функция — единственный способ этого добиться.
    """
    from paxcount.bench.counting import variants_for

    key = "2/2026-09-10T06:58:51/—"
    hand = layout([(500.0, 380.0, 600.0, 700.0)])
    runs = [run_of("owlv2", key, [(505.0, 380.0, 605.0, 700.0)])]
    out = variants_for(key, case_of(size=VehicleSize.SMALL), hand, runs)

    assert out["эталон-двери"] is hand, "потолок — это ручная разметка как есть"
    assert out["owlv2"] is not None
    assert out["groundingdino"] is None, "метод не отвечал — это не ноль дверей"
    assert out["совет"] is None, "совет из одного мнения — не совет"


# ---- Боковой допуск дверной зоны --------------------------------------------
#
# Вертикаль полосы ног измерена: 7116 наблюдений точки опоры относительно кузова.
# Горизонталь не измерялась ни разу — это буквально размеченная ширина проёма.
# На боевых записях проёмы бывают 7 и 12 пикселей (решение 048), и точка опоры
# человека обязана попасть в колонку уже собственной ступни. Замер: на визите 4
# восемь следов умирают в пределах ОДНОГО роста от зоны, при эталоне 7 входов и
# трёх кандидатах.
#
# Допуск задаётся долей ВЫСОТЫ ПОЛОСЫ НОГ, а не пикселями: полоса — доля кузова,
# значит она сама уменьшается вместе с машиной, уехавшей вдаль. Пиксельный
# допуск на дальнем автобусе был бы вдвое шире, чем на ближнем, и означал бы
# разное на разных визитах.

def test_the_default_side_margin_is_the_measured_one():
    """Умолчание — то значение, которое выбрано развёрткой, а не ноль.

    Ноль означал бы «считаем ровно по размеченному проёму», и это уже проверено:
    15 входов из 22. Развёртка по боевым визитам дала 0.25 — восемнадцать из
    двадцати двух без единого ложного (решение 060). Число живёт в одном месте,
    и тест сторожит, что умолчание — именно оно, а не забытая отладочная правка.
    """
    plain = door_specs(layout([(500.0, 380.0, 600.0, 700.0)]))[0]
    band = plain.zone[3] - plain.zone[1]
    assert DOOR_SIDE_MARGIN == 0.25
    assert plain.zone[0] == pytest.approx(500.0 - DOOR_SIDE_MARGIN * band)
    assert plain.zone[2] == pytest.approx(600.0 + DOOR_SIDE_MARGIN * band)


def test_side_margin_widens_the_zone_sideways_only():
    """Допуск — про горизонталь. Вертикаль измерена, её трогать нельзя."""
    plain = door_specs(layout([(500.0, 380.0, 600.0, 700.0)]))[0]
    wide = door_specs(layout([(500.0, 380.0, 600.0, 700.0)]), side_margin=0.5)[0]
    band = plain.zone[3] - plain.zone[1]
    assert wide.zone[0] == pytest.approx(500.0 - 0.5 * band)
    assert wide.zone[2] == pytest.approx(600.0 + 0.5 * band)
    assert (wide.zone[1], wide.zone[3]) == (plain.zone[1], plain.zone[3])


def test_side_margin_shrinks_with_the_vehicle():
    """Дальний автобус — мельче человек — уже допуск. В пикселях так не выйдет."""
    near = door_specs(layout([(500.0, 380.0, 600.0, 700.0)]), side_margin=0.5)[0]
    far = DoorLayout(
        visit_key="2/2026-09-10T06:58:51/—", camera="2", size=VehicleSize.LARGE,
        orientation="нос-справа", video=VIDEO, frame_idx=4956,
        frame_size=(1920, 1080),
        body_px=(BODY[0], BODY[1], BODY[2], BODY[1] + (BODY[3] - BODY[1]) / 2),
        doors=[DoorLayoutEntry(n_from_nose=1, opening_px=(500.0, 380.0, 600.0, 700.0),
                                in_frame=True)],
    )
    far_spec = door_specs(far, side_margin=0.5)[0]
    assert (far_spec.zone[2] - 600.0) * 2 == pytest.approx(near.zone[2] - 600.0)


# ---- Привязка полосы ног: кузов или порог двери ------------------------------
#
# Полоса ног измерена как 0.65..1.12 высоты кузова — то есть отсчитана от НИЗА
# КУЗОВА: от 0.35 высоты выше него до 0.12 ниже. Пока автобус снят сбоку и все
# двери стоят на одной линии, это верно для каждой двери сразу.
#
# На угловом ракурсе не так. Замер по боевым визитам: пороги дверей одного
# автобуса расходятся по высоте кадра на 100-350 px, и у четырёх дверей порог
# лежит ВЫШЕ полосы — на них приходится 2 входа и 2 выхода эталона из 28.
# Человек, вышедший в такую дверь и пошедший вдоль борта, в зону не попадает
# никогда: чтобы попасть, ему надо шагнуть к камере.
#
# Запрет 4 не нарушается: зона остаётся полосой ног, а не дверным проёмом.
# Меняется только то, от чего она отсчитана, — и толщина её та же.

def test_the_band_is_measured_from_the_body_bottom_by_default():
    """Умолчание — как измерено: полоса отсчитана от низа кузова."""
    spec = door_specs(layout([(500.0, 380.0, 600.0, 700.0)]), side_margin=0.0)[0]
    band = DoorSpec.model_fields["zone"].default
    height = BODY[3] - BODY[1]
    assert (spec.zone[1], spec.zone[3]) == pytest.approx(
        (BODY[1] + band[1] * height, BODY[1] + band[3] * height))


def test_per_door_band_keeps_its_thickness_and_moves_to_the_sill():
    """Полоса та же, отсчитана от порога двери, а не от низа кузова."""
    opening = (500.0, 380.0, 600.0, 700.0)
    plain = door_specs(layout([opening]), side_margin=0.0)[0]
    moved = door_specs(layout([opening]), side_margin=0.0, per_door_band=True)[0]
    thickness = plain.zone[3] - plain.zone[1]
    assert moved.zone[3] - moved.zone[1] == pytest.approx(thickness)
    height = BODY[3] - BODY[1]
    above = (1.0 - DoorSpec.model_fields["zone"].default[1]) * height
    assert moved.zone[1] == pytest.approx(opening[3] - above)


def test_per_door_band_changes_nothing_when_the_sill_is_the_body_bottom():
    """Дверь до земли — полоса обязана совпасть с нынешней. Иначе это не та же мера."""
    opening = (500.0, 380.0, 600.0, BODY[3])
    plain = door_specs(layout([opening]), side_margin=0.0)[0]
    moved = door_specs(layout([opening]), side_margin=0.0, per_door_band=True)[0]
    assert moved.zone == pytest.approx(plain.zone)
