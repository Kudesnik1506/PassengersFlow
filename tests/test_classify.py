"""Дефект 4: решение зависело от пары пикселей на старте трека.

Ветка «исчез в зоне двери» требовала, чтобы человек в последних кадрах шёл к
корпусу. Если трек начался чуть раньше — снаружи зоны — и человек успевал
подойти, а в конце топтался вдоль борта, событие отклонялось. Тот же пассажир с
коляской на 09 в одной записи считался, в другой нет, хотя физически событие
одно и то же. Ветку привели к тому же запасному критерию по смещению, что и
в ветке «родился и умер в зоне».

Пороги берутся из модуля, а не переписываются сюда (принцип 2).
"""

from __future__ import annotations

import numpy as np
from conftest import make_life, make_visit, walk
from paxcount.core.types import Direction
from paxcount.zonecount import MIN_TRACK_SECONDS, classify

FRAME_W, FRAME_H = 1920, 1080
# Точки опоры у двери: середина кадра по горизонтали, далеко от краёв.
NEAR_DOOR = (600.0, 720.0)
TOWARD_BUS = (600.0, 640.0)


def judge(life, visit=None):
    return classify(life, visit or make_visit(), FRAME_W, FRAME_H, MIN_TRACK_SECONDS)


def test_disappearing_at_the_door_while_approaching_is_an_entry():
    life = make_life(walk(NEAR_DOOR, TOWARD_BUS), first_in_zone=False, last_in_zone=True)
    direction, reason = judge(life)
    assert direction is Direction.IN
    assert "исчез в зоне двери" in reason


def test_appearing_at_the_door_while_stepping_away_is_an_exit():
    life = make_life(walk(TOWARD_BUS, NEAR_DOOR), first_in_zone=True, last_in_zone=False)
    direction, reason = judge(life)
    assert direction is Direction.OUT
    assert "появился в зоне двери" in reason


def test_entry_survives_a_pause_along_the_body():
    """Регресс дефекта: подошёл к двери, потоптался вдоль борта — и пропал.

    Последние кадры идут поперёк корпуса, поэтому основной критерий («шёл к
    корпусу перед исчезновением») не срабатывает. Но общее смещение трека —
    к корпусу, и запасной критерий обязан засчитать вход. До правки здесь
    возвращалось «вход отклонён».
    """
    approach = walk((600.0, 760.0), (600.0, 660.0), steps=12)
    shuffle = walk((600.0, 660.0), (700.0, 660.0), steps=8)
    life = make_life(approach + shuffle, first_in_zone=False, last_in_zone=True)

    direction, reason = judge(life)
    assert direction is Direction.IN
    assert "исчез в зоне двери" in reason


def test_track_broken_at_the_frame_border_is_not_an_event():
    """Обрыв у края кадра — человек вышел из поля зрения, а не в дверь."""
    life = make_life(
        walk((1700.0, 720.0), (1912.0, 720.0), person_w=60.0),
        first_in_zone=False,
        last_in_zone=True,
    )
    direction, _ = judge(life)
    assert direction is None


def test_too_short_track_is_not_an_event():
    life = make_life(walk(NEAR_DOOR, TOWARD_BUS, steps=3), first_in_zone=False,
                     last_in_zone=True)
    direction, reason = judge(life)
    assert direction is None
    assert reason == "короткий трек"


def test_event_at_the_very_end_of_the_visit_is_suppressed():
    """Трек, оборвавшийся вместе с визитом, — это уход ТС, а не посадка."""
    # Трек живёт 5.00–5.63 с, визит кончается на 6.0 — обрыв попадает в
    # защитную зону у границы визита (0.6 с).
    visit = make_visit(arrival_ts=0.0, departure_ts=6.0)
    life = make_life(walk(NEAR_DOOR, TOWARD_BUS), first_in_zone=False, last_in_zone=True,
                     first_ts=5.0)
    direction, reason = judge(life, visit)
    assert direction is None
    assert reason == "нет события"


def test_passer_by_moving_along_the_body_is_not_an_event():
    """Прохожий вдоль борта: трек рвётся из-за перекрытия, события нет.

    Правило «выход — уход вниз кадра» держится здесь ради этого случая, а не
    ради ракурса. Один проход с ослабленным правилом (удаление от проёма в
    любую сторону) отменён владельцем: на боевом наборе он добавил один
    настоящий выход и тут же дал ложный там, где эталон говорит ноль. Цена
    обратного выбора названа в решении 055 — настоящие выходы вдоль борта не
    засчитываются; возвращать ослабление без нового признака бесполезно.
    """
    life = make_life(
        walk((500.0, 720.0), (760.0, 722.0)),
        first_in_zone=True,
        last_in_zone=False,
    )
    direction, reason = judge(life)
    assert direction is None
    assert "выход отклонён" in reason


# ---- Порог «стоял на месте» — в долях роста, а не в пикселях -----------------
#
# `_by_displacement` — запасной критерий: он решает, куда шёл человек, когда
# прямые признаки направления не сработали. Порог там был задан абсолютом: 20
# пикселей. Ровно от этого бережёт соседний `MIN_SHIFT_RATIO`, у которого в
# комментарии написано «в долях высоты bbox человека — так порог не зависит от
# масштаба сцены».
#
# Цена абсолюта измерена: на визите 3 автобус снят с торца, человек в кадре
# ростом под сотню пикселей, и 20 px — пятая часть его роста. На визите 1
# человек 370 px, и те же 20 px — одна двадцатая. Один порог означает на двух
# записях разное, и визит 3 отдаёт «стоял на месте» там, где человек прошёл
# половину собственного роста.

def scaled_walk(height: float, steps: int, dx: float, dy: float) -> list[np.ndarray]:
    """Человек заданного роста, шагающий на dx/dy ДОЛЕЙ роста за весь путь."""
    return [
        np.array([
            500.0 + dx * height * i / (steps - 1) - height / 6,
            600.0 + dy * height * i / (steps - 1) - height,
            500.0 + dx * height * i / (steps - 1) + height / 6,
            600.0 + dy * height * i / (steps - 1),
        ])
        for i in range(steps)
    ]


def test_standing_still_is_judged_by_person_height_not_pixels():
    """Одно и то же движение у крупного и мелкого человека — один вердикт.

    Движение задано вдоль борта: прямые признаки направления его отклоняют
    («движется вдоль корпуса»), и решение принимает запасной `_by_displacement`
    — тот самый, где стоял абсолютный порог. Человек проходит 0.22 своего
    роста; у крупного это 68 пикселей, у мелкого 17. Абсолютные 20 пикселей
    режут ровно по масштабу сцены, а не по поведению человека.
    """
    visit = make_visit(4.0, 9.0)
    big = make_life(scaled_walk(370.0, 20, 0.22, -0.05), True, True)
    small = make_life(scaled_walk(90.0, 20, 0.22, -0.05), True, True)
    verdicts = [
        classify(life, visit, 1920, 1080, MIN_TRACK_SECONDS)
        for life in (big, small)
    ]
    assert verdicts[0][0] == verdicts[1][0], (
        f"масштаб сцены не смеет менять вердикт: {verdicts}")
    assert verdicts[0][0] is not None, "проход в пятую часть роста — не стояние"
