"""Упаковка кадров для счётной модели: по одной двери за раз, с запасом.

Форма пакета повторяет ручную методику, а не наоборот — методика куплена
ошибками этой сессии:

* **одна дверь — один пакет.** Ручной счёт ведётся по дверям, и журнал
  закрывается, когда журналов столько же, сколько дверей в кадре. Модель,
  которой показывают весь борт сразу, отвечает одним числом, и проверить его
  по дверям уже нечем;
* **кроп с запасом по обе стороны проёма.** Узкий кроп по самому проёму —
  замеренная причина недосчёта: человек, идущий вдоль борта, появляется в
  кадре уже внутри проёма, и вход от прохода мимо не отличить;
* **дверь за кадром не упаковывается вовсе.** Пакета нет — значит, счёта нет,
  а не ноль: ноль означал бы «посмотрели, там пусто».

Шаг по времени берётся из `settings.PACKAGE_FPS` — не копия константы, а
импорт: разъехавшиеся копии частоты дают пакет, оценка стоимости которого не
совпадает с ним самим.
"""

from __future__ import annotations

import importlib.util

import numpy as np
import pytest
from paxcount.counting.packages import build_packages, crop_box
from paxcount.delivery.model import VehicleSize
from paxcount.settings import CROP_MARGIN_PX
from paxcount.truth import DoorLayout, DoorLayoutEntry
from paxcount.windows import Window

FRAME_W, FRAME_H = 1920, 1080

# Пиксели трогает только часть тестов. Тестовая группа проекта ставится без
# OpenCV (см. pyproject: numpy, pydantic, pandas, rich) — геометрия кропа и
# оценка стоимости обязаны проверяться и там, а нарезка кадров пропускается.
needs_cv2 = pytest.mark.skipif(
    importlib.util.find_spec("cv2") is None,
    reason="нарезка кадров требует OpenCV, в тестовой группе его нет",
)


def frames(count: int = 40, fps: float = 10.0):
    """Синтетическая лента: кадр, время, картинка — как `core.video.frames`."""
    for i in range(count):
        picture = np.full((FRAME_H, FRAME_W, 3), 30, dtype=np.uint8)
        picture[:, i * 10 : i * 10 + 40] = 200  # что-то движется, чтобы кропы различались
        yield i, i / fps, picture


def layout(**over) -> DoorLayout:
    base = dict(
        visit_key="2/07:02:06/1596", camera="2", size=VehicleSize.LARGE,
        orientation="нос-слева",
        video="2026-09-10 - 06-56-06 - 22739_2 - 02", frame_idx=7326,
        frame_size=(1920, 1080),
        doors=[
            DoorLayoutEntry(n_from_nose=1, opening_px=(300.0, 400.0, 420.0, 800.0),
                            in_frame=True),
            DoorLayoutEntry(n_from_nose=2, opening_px=(700.0, 400.0, 820.0, 800.0),
                            in_frame=True),
            DoorLayoutEntry(n_from_nose=3, opening_px=None, in_frame=False),
        ],
    )
    base.update(over)
    return DoorLayout(**base)


def window(**over) -> Window:
    base = dict(visit_id=7, t0=1.0, t1=3.0, box=(300.0, 400.0, 1500.0, 800.0),
                person_px=120.0, rivals=(), contested=False)
    base.update(over)
    return Window(**base)


# ---- Кроп ------------------------------------------------------------------


def test_crop_adds_margin_on_both_sides():
    box = crop_box((700.0, 400.0, 820.0, 800.0), (FRAME_W, FRAME_H))
    assert box[0] == pytest.approx(700.0 - CROP_MARGIN_PX)
    assert box[2] == pytest.approx(820.0 + CROP_MARGIN_PX)


def test_crop_is_clamped_to_the_frame():
    """Дверь у края кадра: запас упирается в границу, а не уходит в минус."""
    box = crop_box((20.0, 400.0, 140.0, 800.0), (FRAME_W, FRAME_H))
    assert box[0] == 0.0
    assert box[2] <= FRAME_W


def test_crop_margin_comes_from_settings_not_a_local_copy():
    """Запрет 9: порог импортируется, а не копируется."""
    import inspect

    from paxcount.counting import packages

    source = inspect.getsource(packages)
    assert "CROP_MARGIN_PX" in source
    assert "140" not in source, "число не должно быть вписано в модуль второй раз"


# ---- Пакеты ----------------------------------------------------------------


@needs_cv2
def test_one_package_per_visible_door():
    packages = build_packages(frames(), window(), layout())
    assert [p.door for p in packages] == [1, 2]


@needs_cv2
def test_door_out_of_frame_produces_no_package():
    """Пакета нет — значит счёта нет. Это не ноль."""
    packages = build_packages(frames(), window(), layout())
    assert all(p.door != 3 for p in packages)


@needs_cv2
def test_frames_are_sampled_inside_the_window_only():
    packages = build_packages(frames(), window(t0=1.0, t1=2.0))
    times = [f.t for f in packages[0].frames]
    assert times, "окно не пустое"
    assert min(times) >= 0.0
    assert max(times) <= 1.0 + 1e-6, "время внутри пакета отсчитывается от начала окна"


@needs_cv2
def test_frame_count_follows_package_fps():
    from paxcount.settings import PACKAGE_FPS

    packages = build_packages(frames(count=200, fps=25.0), window(t0=0.0, t1=2.0))
    # 2 секунды при PACKAGE_FPS кадров в секунду, плюс-минус один кадр на краях.
    assert abs(len(packages[0].frames) - int(2 * PACKAGE_FPS)) <= 1


@needs_cv2
def test_frames_carry_encoded_jpeg_bytes():
    packages = build_packages(frames(), window(), layout())
    first = packages[0].frames[0]
    assert isinstance(first.jpeg, bytes) and len(first.jpeg) > 0
    assert first.jpeg[:2] == b"\xff\xd8", "это должен быть JPEG"


@needs_cv2
def test_wide_crop_is_scaled_down_to_the_package_width():
    """Кроп шире `PACKAGE_MAX_WIDTH` уменьшается: иначе платим за пиксели зря."""
    from paxcount.settings import PACKAGE_MAX_WIDTH

    wide = layout(doors=[DoorLayoutEntry(
        n_from_nose=1, opening_px=(0.0, 0.0, 1900.0, 1000.0), in_frame=True)],
        size=VehicleSize.SMALL)
    packages = build_packages(frames(), window(), wide)
    assert packages[0].width <= PACKAGE_MAX_WIDTH


@needs_cv2
def test_package_estimates_its_own_token_cost():
    packages = build_packages(frames(), window(), layout())
    assert packages[0].tokens_estimate() > 0


@needs_cv2
def test_empty_window_yields_no_frames():
    packages = build_packages(frames(), window(t0=5.0, t1=5.0), layout())
    assert all(len(p.frames) == 0 for p in packages)


@needs_cv2
def test_layout_without_visible_doors_yields_nothing():
    blind = layout(doors=[DoorLayoutEntry(n_from_nose=1, opening_px=None, in_frame=False)],
                    size=VehicleSize.SMALL)
    assert build_packages(frames(), window(), blind) == []
