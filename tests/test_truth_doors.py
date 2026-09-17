"""Разметка дверей на визит эталона — и арифметика, которая ловит потерю двери.

Замер этой сессии: у борта 7861 (большой автобус, 3 двери) передняя дверь
осталась за краем ручного кропа, потому что разметка делалась «на глаз» после
взгляда на кадр, а не файлом до счёта. Итог нашёлся только когда число дверей
по размеру (таблица 3) сравнили со счётом того, что размечено — по отдельности
в кадре и за краем. Метод 1-2 сессии: разметка — файл, полнота — арифметика.

Разметка — на ВИЗИТ, а не на видео: одна и та же камера снимает и машину,
вставшую перед опорой, и машину, вставшую за ней, и общего дверного проёма у
них нет.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from paxcount.delivery.model import VehicleSize
from paxcount.truth import DoorLayout, DoorLayoutEntry, check_door_arithmetic


def entry(**over) -> DoorLayoutEntry:
    base = dict(n_from_nose=1, opening_px=(400.0, 500.0, 460.0, 780.0), in_frame=True)
    base.update(over)
    return DoorLayoutEntry(**base)


# Кузов посреди кадра 1920x1080: до краёв далеко, обрезки нет.
BODY = (300.0, 400.0, 1500.0, 800.0)
FRAME = (1920, 1080)


def layout(**over) -> DoorLayout:
    base = dict(
        visit_key="2/2026-09-10T07:02:06/1596", camera="2", size=VehicleSize.LARGE,
        orientation="нос-справа",
        video="2026-09-10 - 06-56-06 - 22739_2 - 02", frame_idx=7326,
        frame_size=FRAME, body_px=BODY,
        doors=[entry(n_from_nose=1), entry(n_from_nose=2), entry(n_from_nose=3)],
    )
    base.update(over)
    return DoorLayout(**base)


# ---- Форма записи -----------------------------------------------------------


def test_door_not_in_frame_has_no_pixels():
    door = entry(in_frame=False, opening_px=None)
    assert door.opening_px is None


def test_door_marked_in_frame_needs_pixels():
    """В кадре, но без координат — это недоразметка, а не законное состояние."""
    with pytest.raises(ValueError):
        entry(in_frame=True, opening_px=None)


def test_door_marked_out_of_frame_must_not_carry_pixels():
    """Координаты у двери, помеченной вне кадра, — противоречие, а не деталь."""
    with pytest.raises(ValueError):
        entry(in_frame=False, opening_px=(1.0, 2.0, 3.0, 4.0))


# ---- Арифметика: таблица 3 = в кадре + за краем ------------------------------


def test_complete_layout_for_a_large_bus_passes():
    assert check_door_arithmetic(layout()) == []


def test_missing_door_is_caught_by_the_count_not_by_eye():
    """7861: три двери по размеру, но в разметке — только две. Это и есть тот случай."""
    incomplete = layout(doors=[entry(n_from_nose=1), entry(n_from_nose=2)])
    problems = check_door_arithmetic(incomplete)
    assert problems and "2" in problems[0] and "3" in problems[0]


def test_out_of_frame_doors_still_count_toward_the_total():
    """Дверь за краем кадра тоже часть арифметики — не только видимые.

    Кузов здесь обрезан левым краем: иначе сработает соседняя проверка, и тест
    начнёт падать по чужой причине.
    """
    mixed = layout(orientation="нос-слева", body_px=(0.0, 400.0, 1500.0, 800.0), doors=[
        entry(n_from_nose=1, in_frame=False, opening_px=None),
        entry(n_from_nose=2), entry(n_from_nose=3),
    ])
    assert check_door_arithmetic(mixed) == []


def test_articulated_bus_needs_four_doors():
    xlarge = layout(size=VehicleSize.XLARGE,
                     doors=[entry(n_from_nose=i) for i in (1, 2, 3)])
    problems = check_door_arithmetic(xlarge)
    assert problems and "4" in problems[0]


def test_rail_sizes_have_no_door_count_to_check():
    """Вагоны считаются по гармошкам, а не по дверям — арифметика тут не судья."""
    tram = layout(size=VehicleSize.ONE_CAR, doors=[entry(n_from_nose=1)])
    assert check_door_arithmetic(tram) == []


def test_duplicate_door_numbers_are_a_problem():
    dup = layout(doors=[entry(n_from_nose=1), entry(n_from_nose=1), entry(n_from_nose=3)])
    problems = check_door_arithmetic(dup)
    assert problems and any("повтор" in p.lower() for p in problems)


def test_gap_in_door_numbering_is_a_problem():
    """1, 2, 4 — пропущена третья дверь в нумерации, не только в счёте."""
    gap = layout(doors=[entry(n_from_nose=1), entry(n_from_nose=2), entry(n_from_nose=4)])
    problems = check_door_arithmetic(gap)
    assert problems and any("нумерац" in p.lower() for p in problems)


# ---- Хранение: data/truth/doors/<камера>/<stop_ts>.json ----------------------


def test_layout_round_trips_through_json(tmp_path):
    from paxcount.truth import load_door_layout, save_door_layout

    path = tmp_path / "2" / "2026-09-10T07-02-06.json"
    save_door_layout(path, layout())
    assert load_door_layout(path) == layout()


# ---- Кадр, на котором нарисованы пиксели ------------------------------------


def test_layout_names_the_frame_its_pixels_belong_to():
    """Пиксели без кадра ничего не значат: их не проверить и не нарезать.

    Кроп для счёта режется из конкретного кадра конкретного файла. Разметка,
    не называющая ни файла, ни номера кадра, годится ровно до конца сессии, в
    которой её сделали, — а эталон живёт дольше.
    """
    marked = layout()
    assert marked.video.endswith("22739_2 - 02")
    assert marked.frame_idx == 7326

    with pytest.raises(ValueError):
        DoorLayout(
            visit_key="2/2026-09-10T07:02:06/1596", camera="2",
            size=VehicleSize.LARGE, orientation="нос-справа",
            doors=[entry(n_from_nose=n) for n in (1, 2, 3)],
        )


def test_body_box_is_checked_for_sanity():
    """Перевёрнутая рамка кузова — опечатка разметчика, а не узкий кузов."""
    with pytest.raises(ValueError):
        layout(body_px=(1500.0, 400.0, 300.0, 800.0))


# ---- «Дверь за кадром» проверяется рамкой кузова -----------------------------


def test_out_of_frame_door_on_a_fully_visible_body_is_a_problem():
    """Кузов целиком в кадре — значит, все его двери тоже. Решение 038, но рукой.

    Для автопредложения это уже запрещено: промах метода нельзя записывать как
    дверь за кадром, иначе недосчёт становится законным и уходит в отчёт кодом
    5-8. У ручной разметки соблазн тот же и цена та же — разметчику проще
    поставить галочку «не видно», чем найти третью дверь в глубине кадра.
    """
    hidden = layout(doors=[
        entry(n_from_nose=1), entry(n_from_nose=2),
        entry(n_from_nose=3, in_frame=False, opening_px=None),
    ])
    problems = check_door_arithmetic(hidden)
    assert problems and any("не доходит до кра" in p for p in problems)


def test_out_of_frame_door_is_legal_when_the_body_is_cut_by_the_edge():
    """Кузов упёрся в левый край, нос слева — дверь 1 за кадром законна."""
    cut = layout(orientation="нос-слева", body_px=(0.0, 400.0, 1500.0, 800.0), doors=[
        entry(n_from_nose=1, in_frame=False, opening_px=None),
        entry(n_from_nose=2), entry(n_from_nose=3),
    ])
    assert check_door_arithmetic(cut) == []


def test_without_a_body_box_the_edge_rule_stays_silent():
    """Рамка кузова не размечена — судить об обрезке нечем, выдумывать нельзя."""
    unknown = layout(body_px=None, doors=[
        entry(n_from_nose=1), entry(n_from_nose=2),
        entry(n_from_nose=3, in_frame=False, opening_px=None),
    ])
    assert check_door_arithmetic(unknown) == []


def test_door_pixels_must_lie_inside_the_body():
    """Проём вне рамки кузова — разметка соседней машины или промах мыши."""
    stray = layout(doors=[
        entry(n_from_nose=1, opening_px=(1700.0, 500.0, 1800.0, 780.0)),
        entry(n_from_nose=2), entry(n_from_nose=3),
    ])
    problems = check_door_arithmetic(stray)
    assert problems and any("кузов" in p.lower() for p in problems)


# ---- Путь файла разметки ----------------------------------------------------


def test_path_is_derived_from_camera_and_visit_key():
    from paxcount.truth import door_layout_path

    path = door_layout_path("2", "2/2026-09-10T07:02:06/1596", root=Path("data/truth/doors"))
    assert path.parent.name == "2"
    assert path.suffix == ".json"
    assert ":" not in path.name and "/" not in path.name


def test_different_visits_never_share_a_file():
    from paxcount.truth import door_layout_path

    root = Path("data/truth/doors")
    first = door_layout_path("2", "2/2026-09-10T07:02:06/1596", root=root)
    second = door_layout_path("2", "2/2026-09-10T07:04:55/7861", root=root)
    assert first != second


# ---- «За кадром» — с той стороны, где кадр и обрезан ------------------------


def test_hidden_nose_door_needs_truncation_on_the_nose_side():
    """Кузов обрезан справа, нос слева — первая дверь за кадром быть не может.

    Найдено на первом же файле разметки: кузов упёрся в правый край, а «вне
    кадра» отмечена дверь 1. Проверка «обрезан ли кузов вообще» это пропускала,
    и разметка уходила в эталон с потерянной левой дверью — ровно тот случай,
    ради которого проверка и написана.
    """
    wrong = layout(orientation="нос-слева",
                    body_px=(738.0, 448.0, 1920.0, 1016.0), doors=[
        entry(n_from_nose=1, in_frame=False, opening_px=None),
        entry(n_from_nose=2, opening_px=(900.0, 500.0, 980.0, 900.0)),
        entry(n_from_nose=3, opening_px=(1500.0, 500.0, 1580.0, 900.0)),
    ])
    problems = check_door_arithmetic(wrong)
    assert problems and any("сторон" in p.lower() for p in problems)


def test_hidden_nose_door_is_fine_when_that_side_is_cut():
    """Тот же кузов, но обрезан слева — теперь первая дверь за кадром законна."""
    right = layout(orientation="нос-слева",
                    body_px=(0.0, 448.0, 1500.0, 1016.0), doors=[
        entry(n_from_nose=1, in_frame=False, opening_px=None),
        entry(n_from_nose=2, opening_px=(400.0, 500.0, 480.0, 900.0)),
        entry(n_from_nose=3, opening_px=(1000.0, 500.0, 1080.0, 900.0)),
    ])
    assert check_door_arithmetic(right) == []


def test_nose_on_the_right_mirrors_the_rule():
    """Нос справа: первая дверь — правая, и обрезан должен быть правый край."""
    right = layout(orientation="нос-справа",
                    body_px=(738.0, 448.0, 1920.0, 1016.0), doors=[
        entry(n_from_nose=1, in_frame=False, opening_px=None),
        entry(n_from_nose=2, opening_px=(1500.0, 500.0, 1580.0, 900.0)),
        entry(n_from_nose=3, opening_px=(900.0, 500.0, 980.0, 900.0)),
    ])
    assert check_door_arithmetic(right) == []


def test_hidden_door_between_visible_ones_is_never_out_of_frame():
    """Средняя дверь не может быть за краем кадра: по краям от неё видимые."""
    impossible = layout(body_px=(0.0, 448.0, 1500.0, 1016.0), doors=[
        entry(n_from_nose=1, opening_px=(200.0, 500.0, 280.0, 900.0)),
        entry(n_from_nose=2, in_frame=False, opening_px=None),
        entry(n_from_nose=3, opening_px=(1000.0, 500.0, 1080.0, 900.0)),
    ])
    problems = check_door_arithmetic(impossible)
    assert problems and any("между" in p.lower() for p in problems)


# ---- Замечание обязано называть расстояние, а не выносить приговор ----------


def test_the_complaint_states_how_far_the_body_is_from_each_edge():
    """«Кузов виден целиком» при зазоре в 29 px — неправда, и она сбивает с толку.

    Замерено на боевой разметке борта 7326 (К2, кадр 1530): кузов обрезан левым
    краем, но рамка проведена рукой на 29 px внутрь. Порог «упёрся в край» — 8 px,
    он взят для рамок детектора; рукой на вписанном кадре, где экранный пиксель
    равен двум кадровым, в него не попасть. Вместо приговора замечание должно
    показать числа и сказать, что делать.
    """
    almost = layout(orientation="нос-справа",
                     body_px=(29.0, 0.0, 1744.0, 1068.0), doors=[
        entry(n_from_nose=1, opening_px=(1017.0, 235.0, 1584.0, 1065.0)),
        entry(n_from_nose=2, opening_px=(40.0, 121.0, 212.0, 652.0)),
        entry(n_from_nose=3, in_frame=False, opening_px=None),
    ])
    problems = check_door_arithmetic(almost)
    assert problems
    text = problems[0]
    assert "29" in text and "176" in text, "названы оба зазора до краёв кадра"
    assert "целиком" not in text, "29 px до края — это не «виден целиком»"
    assert "за границ" in text.lower(), "сказано, как исправить"


def test_a_body_far_from_every_edge_still_gets_the_numbers():
    """Настоящий «виден целиком» тоже показывает зазоры — судить читающему."""
    problems = check_door_arithmetic(layout(doors=[
        entry(n_from_nose=1), entry(n_from_nose=2),
        entry(n_from_nose=3, in_frame=False, opening_px=None),
    ]))
    assert problems and "300" in problems[0] and "420" in problems[0]


# ---- Один визит — одна запись ------------------------------------------------


def saved(key: str, body, frame: int) -> DoorLayout:
    return layout(visit_key=key, frame_idx=frame, body_px=body,
                   orientation="нос-слева",
                   doors=[entry(n_from_nose=n) for n in (1, 2, 3)])


def test_same_vehicle_marked_twice_is_caught_before_it_becomes_two_visits():
    """Шаг на секунду и повторное сохранение давали вторую запись того же визита.

    Замерено на боевой разметке: борт 38142 записан кадрами 4926 и 4956 —
    рамка кузова и все три двери совпали до пикселя, а ключи разошлись, потому
    что строятся из точного времени кадра. В стенде эта машина пошла бы дважды,
    в эталоне вместо трёх визитов значилось бы четыре.
    """
    from paxcount.truth import duplicate_visit_problems

    body = (4.0, 187.0, 900.0, 1072.0)
    existing = [saved("2/2026-09-10T06:58:50/—", body, 4926)]
    again = saved("2/2026-09-10T06:58:51/—", body, 4956)
    problems = duplicate_visit_problems(again, existing)
    assert problems and "06:58:50" in problems[0]


def test_resaving_the_same_visit_is_an_update_not_a_duplicate():
    """Тот же ключ — это правка записи, а не вторая машина."""
    from paxcount.truth import duplicate_visit_problems

    body = (4.0, 187.0, 900.0, 1072.0)
    existing = [saved("2/2026-09-10T06:58:50/—", body, 4926)]
    assert duplicate_visit_problems(saved("2/2026-09-10T06:58:50/—", body, 4926),
                                     existing) == []


def test_a_vehicle_stopped_elsewhere_is_not_a_duplicate():
    """Машина, вставшая в другом месте кадра, — другой визит, вопросов нет."""
    from paxcount.truth import duplicate_visit_problems

    existing = [saved("2/2026-09-10T06:58:50/—", (4.0, 187.0, 900.0, 1072.0), 4926)]
    far = saved("2/2026-09-10T07:02:06/—", (1100.0, 400.0, 1800.0, 900.0), 9000)
    assert duplicate_visit_problems(far, existing) == []


def test_a_vehicle_overlapping_a_previous_one_is_not_a_duplicate():
    """Борт 7326 заполняет почти весь кадр — внутри него центр любой машины.

    Первая версия проверки ловила пересечение рамок и поэтому ругалась на
    каждый второй визит той же камеры. Предупреждение, срабатывающее всегда,
    перестают читать — а вместе с ним пропускают и настоящий дубль.
    """
    from paxcount.truth import duplicate_visit_problems

    huge = [saved("2/2026-09-10T06:56:57/—", (29.0, 0.0, 1744.0, 1068.0), 1530)]
    smaller = saved("2/2026-09-10T06:58:50/—", (4.0, 187.0, 900.0, 1072.0), 4926)
    assert duplicate_visit_problems(smaller, huge) == []


def test_a_nudge_of_a_pixel_is_still_the_same_markup():
    """Сдвиг на полпикселя при перерисовке не делает запись новым визитом."""
    from paxcount.truth import duplicate_visit_problems

    existing = [saved("2/2026-09-10T06:58:50/—", (4.0, 187.0, 900.0, 1072.0), 4926)]
    again = saved("2/2026-09-10T06:58:51/—", (4.4, 187.2, 900.3, 1071.6), 4956)
    assert duplicate_visit_problems(again, existing)


def test_redrawn_body_is_still_the_same_visit():
    """Главный случай: разметку исправили, и она ушла в НОВЫЙ файл.

    Замерено на борте 7326. Владелец дотянул рамку кузова до края кадра и
    сохранил — но кадр к тому моменту сместился на два секунды (1530 → 1590),
    ключ визита сменился, и вместо правки получилась вторая запись. Проверка
    «геометрия совпадает до пикселя» этого не ловила: рамка-то изменилась.

    Мера — перекрытие рамок кузова. На боевых данных оно разделяет случаи с
    запасом: та же машина, перерисованная, дала 0.98; две разные машины на
    одной камере — 0.41 и 0.08.
    """
    from paxcount.truth import duplicate_visit_problems

    existing = [saved("2/2026-09-10T06:56:57/—", (29.0, 0.0, 1744.0, 1068.0), 1530)]
    fixed = saved("2/2026-09-10T06:56:59/—", (0.0, 0.0, 1744.0, 1068.0), 1590)
    problems = duplicate_visit_problems(fixed, existing)
    assert problems and "06:56:57" in problems[0]


def test_the_threshold_sits_between_measured_cases():
    """Порог не выбран на глаз: оба его края замерены на боевой разметке."""
    from paxcount.truth import SAME_VISIT_GAP_S, SAME_VISIT_IOU

    assert 0.413 < SAME_VISIT_IOU < 0.983
    # Две записи одного визита отстояли на 1-2 с; ближайшие РАЗНЫЕ визиты
    # той же камеры — на 43 с. Замеренная стоянка длится 11-20 с.
    assert 2.0 < SAME_VISIT_GAP_S < 43.0


def test_two_vehicles_on_the_same_spot_minutes_apart_are_not_a_duplicate():
    """Бортов 38142 и 38157 разделяют пять минут, а рамки почти совпадают.

    Замерено: перекрытие рамок этих двух машин выше порога — они вставали на
    одно место. Одной геометрии для дубля мало, нужен и разрыв во времени:
    вторая запись одного визита отстоит на секунды, другая машина — на минуты.
    """
    from paxcount.truth import duplicate_visit_problems

    body = (4.0, 187.0, 900.0, 1072.0)
    existing = [saved("2/2026-09-10T06:58:51/—", body, 4956)]
    later = saved("2/2026-09-10T07:04:07/—", (0.0, 191.0, 910.0, 1072.0), 14436)
    assert duplicate_visit_problems(later, existing) == []


def test_another_camera_is_never_a_duplicate():
    """К2 и К3 видят разные позиции стоянки: совпадение координат ничего не значит."""
    from paxcount.truth import duplicate_visit_problems

    body = (4.0, 187.0, 900.0, 1072.0)
    existing = [saved("2/2026-09-10T06:58:50/—", body, 4926)]
    other = saved("3/2026-09-10T06:58:50/—", body, 4926)
    other = other.model_copy(update={"camera": "3", "video": "другое видео"})
    assert duplicate_visit_problems(other, existing) == []
