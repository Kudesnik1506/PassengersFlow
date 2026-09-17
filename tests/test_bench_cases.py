"""Случаи теста собираются из ручной разметки эталона — и только из неё.

Вход у всех сравниваемых методов один: кадр и рамка кузова на нём. Рамка
берётся из разметки человека, а не из детектора: кэша детекции по боевым
записям нет, а сравнивать методы на разных рамках бессмысленно — разница в
рамке превратится в разницу в качестве метода.

Отсюда и главное правило модуля: разметка без рамки кузова случаем не
становится. Пропустить её молча нельзя — иначе таблица результатов окажется
построенной на меньшем числе машин, чем думает читающий.
"""

from __future__ import annotations

from paxcount.bench.cases import load_cases
from paxcount.delivery.model import VehicleSize
from paxcount.truth import DoorLayout, DoorLayoutEntry, door_layout_path, save_door_layout

FRAME = (1920, 1080)
BODY = (300.0, 400.0, 1500.0, 800.0)


def door(n: int, x0: float, visible: bool = True) -> DoorLayoutEntry:
    box = (x0, 500.0, x0 + 80.0, 780.0) if visible else None
    return DoorLayoutEntry(n_from_nose=n, opening_px=box, in_frame=visible)


def layout(**over) -> DoorLayout:
    base = dict(
        visit_key="2/2026-09-10T07:04:01/38157", camera="2",
        size=VehicleSize.LARGE, orientation="нос-слева",
        video="2026-09-10 - 06-56-06 - 22739_2 - 02", frame_idx=14275,
        frame_size=FRAME, body_px=BODY,
        doors=[door(1, 400.0), door(2, 800.0), door(3, 1200.0)],
    )
    base.update(over)
    return DoorLayout(**base)


def put(root, marked: DoorLayout) -> None:
    save_door_layout(door_layout_path(marked.camera, marked.visit_key, root=root), marked)


# ---- Сборка случая ----------------------------------------------------------


def test_case_carries_the_frame_and_the_body_box(tmp_path):
    put(tmp_path, layout())
    cases, skipped = load_cases(tmp_path)
    assert skipped == []
    case = cases[0]
    assert case.video == "2026-09-10 - 06-56-06 - 22739_2 - 02"
    assert case.frame_idx == 14275
    assert case.body_px == BODY
    assert case.frame_size == FRAME


def test_only_visible_doors_become_the_reference(tmp_path):
    """Дверь за краем кадра метод найти не мог — её нет в знаменателе."""
    put(tmp_path, layout(body_px=(0.0, 400.0, 1500.0, 800.0), doors=[
        door(1, 0.0, visible=False), door(2, 800.0), door(3, 1200.0),
    ]))
    cases, _ = load_cases(tmp_path)
    assert len(cases[0].doors_visible) == 2
    assert cases[0].doors_total == 3, "по таблице 3 дверей всё равно три"


def test_layout_without_a_body_box_is_not_a_case_and_is_named(tmp_path):
    """Молчаливый пропуск дал бы таблицу по меньшему числу машин, чем кажется."""
    put(tmp_path, layout(body_px=None))
    cases, skipped = load_cases(tmp_path)
    assert cases == []
    assert skipped and "рамк" in skipped[0].lower()


def test_layout_without_a_single_visible_door_is_not_a_case(tmp_path):
    """Сравнивать не с чем: эталон пуст, и «ноль из нуля» — не результат."""
    put(tmp_path, layout(body_px=(0.0, 400.0, 1500.0, 800.0), doors=[
        door(n, 0.0, visible=False) for n in (1, 2, 3)
    ]))
    cases, skipped = load_cases(tmp_path)
    assert cases == []
    assert skipped and "двер" in skipped[0].lower()


def test_cases_are_ordered_by_camera_and_frame(tmp_path):
    put(tmp_path, layout(visit_key="2/2026-09-10T07:04:01/38157", frame_idx=14275))
    put(tmp_path, layout(visit_key="2/2026-09-10T06:56:54/7326", frame_idx=1464))
    cases, _ = load_cases(tmp_path)
    assert [c.frame_idx for c in cases] == [1464, 14275]


def test_missing_directory_yields_nothing_rather_than_failing(tmp_path):
    cases, skipped = load_cases(tmp_path / "нет-такой")
    assert cases == [] and skipped == []


# ---- Камеры, по которым счёт не ведётся --------------------------------------


def test_layout_of_a_non_counting_camera_is_skipped_with_a_reason(tmp_path):
    """Разметка К1 не участвует в сравнении: по ней всё равно не считают (030).

    Пропуск обязан быть назван. Молча выброшенный случай даёт таблицу по
    меньшему числу машин, чем думает читающий, — а на выборке в пять машин это
    и есть весь вывод.
    """
    from paxcount.cameras import COUNTING_CAMERAS

    put(tmp_path, layout(visit_key="1/2026-09-10T06:56:30/—", camera="1"))
    cases, skipped = load_cases(tmp_path, cameras=COUNTING_CAMERAS)
    assert cases == []
    assert skipped and "камер" in skipped[0].lower()


def test_without_a_filter_every_camera_is_loaded(tmp_path):
    """Отбор — дело вызывающего: сам загрузчик политики камер не знает."""
    put(tmp_path, layout(visit_key="1/2026-09-10T06:56:30/—", camera="1"))
    cases, skipped = load_cases(tmp_path)
    assert len(cases) == 1 and skipped == []
