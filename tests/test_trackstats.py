"""Качество следов: рвутся они или поглощаются.

Это прибор, а не правило счёта. Он отвечает на один вопрос, от которого зависит,
есть ли смысл в покадровой детекции. Решение 001 замерило, что «теряется не
обнаружение, а удержание личности»; решение 002 уточнило механизм — «человек не
разрывается надвое, а поглощается перекрытием внутри чужого трека». Два эти
исхода лечатся по-разному:

* **разрыв** — след оборвался, и рядом почти сразу родился другой: личность
  передана новому идентификатору. Чаще кадры — реже разрывы;
* **поглощение** — след оборвался, и никто рядом не родился: человек растворился
  в чужой рамке. Частота кадров тут ни при чём.

Третий исход не потеря вовсе: **ушёл за край кадра**. Смешать его с
поглощением значит записать в потери каждого, кто просто вышел из поля зрения,
и получить бодрое число, которое ничего не значит.

Пороги соседства берутся у `zonecount` импортом, а не копией (запрет 8): «рядом
по времени и месту» здесь то же самое, что там, и разъехаться эти два понятия
не должны.
"""

from __future__ import annotations

import numpy as np
from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.trackstats import absorption_kinds, track_stats

FPS = 30.0
W, H = 1920, 1080


def boxes_scene(tracks: dict[int, tuple[int, list[tuple[float, float, float, float]]]],
                 tail: int = 15) -> TrackData:
    """Сцена из явных рамок: id → (кадр рождения, рамки по кадрам).

    Нужна там, где важен РАЗМЕР рамки, а не только её место: слияние двух
    людей в одну рамку видно ровно по тому, что рамка соседа выросла.

    `tail` — сколько пустых кадров после последнего следа. По умолчанию 15
    (0.5 с при 30 к/с), то есть заведомо больше порога соседства: иначе любая
    смерть попадёт в «окно кончилось раньше человека». Тест про край окна
    задаёт хвост короче намеренно.
    """
    frames: list[FrameTracks] = []
    last = max(start + len(boxes) for start, boxes in tracks.values())
    for idx in range(last + tail):
        ids, boxes = [], []
        for tid, (start, track_boxes) in tracks.items():
            if start <= idx < start + len(track_boxes):
                ids.append(tid)
                boxes.append(track_boxes[idx - start])
        frames.append(FrameTracks(
            frame_idx=idx, ts=idx / FPS,
            person_ids=np.array(ids, dtype=int),
            person_boxes=np.array(boxes, dtype=float).reshape(-1, 4),
            person_conf=np.ones(len(ids)),
            vehicle_ids=np.zeros(0, dtype=int),
            vehicle_boxes=np.zeros((0, 4)), vehicle_names=[],
        ))
    return TrackData(video="synthetic", width=W, height=H, fps=FPS, stride=1,
                      frames=frames)


def scene(tracks: dict[int, tuple[int, list[tuple[float, float]]]],
           tail: int = 15) -> TrackData:
    """Сцена из следов: id → (кадр рождения, точки опоры по кадрам).

    Рамка человека строится вокруг точки опоры ростом 120 px — так же, как в
    `conftest.walk`, чтобы радиус соседства считался от той же величины.
    """
    return boxes_scene(
        {tid: (start, [(x - 20.0, y - 120.0, x + 20.0, y) for x, y in points])
         for tid, (start, points) in tracks.items()},
        tail=tail,
    )


def straight(x0: float, y: float, dx: float, n: int) -> list[tuple[float, float]]:
    return [(x0 + dx * i, y) for i in range(n)]


def test_track_leaving_the_frame_is_not_a_loss():
    """Ушёл за край кадра — вышел из поля зрения, а не потерян."""
    data = scene({1: (0, straight(1700.0, 600.0, 15.0, 20))})
    stats = track_stats(data)
    assert stats.at_border == 1
    assert stats.vanishings == 0 and stats.handovers == 0


def test_death_with_a_successor_nearby_is_a_handover():
    """Оборвался и тут же родился рядом другой — это передача личности."""
    data = scene({
        1: (0, straight(600.0, 600.0, 10.0, 20)),
        2: (23, straight(800.0, 600.0, 10.0, 20)),
    }, tail=2)   # второй доживает до конца окна и сам в потери не идёт
    stats = track_stats(data)
    assert stats.handovers == 1, "смерть 1 объясняется рождением 2"
    assert stats.vanishings == 0


def test_death_without_a_successor_is_an_absorption():
    """Оборвался посреди кадра, и никто рядом не родился — поглощение."""
    data = scene({
        1: (0, straight(600.0, 600.0, 10.0, 20)),
        2: (23, straight(1500.0, 300.0, 10.0, 20)),   # далеко, не наследник
    }, tail=2)
    stats = track_stats(data)
    assert stats.vanishings == 1
    assert stats.handovers == 0


def test_track_alive_at_the_end_of_the_window_is_not_a_loss():
    """Окно кончилось раньше человека — это граница записи, а не потеря."""
    data = scene({1: (0, straight(600.0, 600.0, 10.0, 200))}, tail=2)
    stats = track_stats(data)
    assert stats.vanishings == 0 and stats.handovers == 0
    assert stats.alive_at_edge == 1


def test_fragments_are_counted_in_seconds_not_frames():
    """Обрывок — это короткий по ВРЕМЕНИ след, иначе мера поедет от частоты."""
    data = scene({
        1: (0, straight(600.0, 600.0, 10.0, 6)),      # 0.17 с — обрывок
        2: (40, straight(300.0, 900.0, 10.0, 40)),    # 1.3 с — полноценный
    }, tail=2)
    stats = track_stats(data)
    assert stats.tracks == 2
    assert stats.fragments == 1


def test_only_tracks_that_touched_a_door_zone_are_counted():
    """Толпа на тротуаре в двадцати метрах от машины к счёту не относится.

    Без отбора по зоне числа считаются по всем следам кадра, а их на боевой
    остановке сотни. Вывод о качестве следов надо делать по тем, кто вообще
    подходил к двери, — иначе меряется людность улицы, а не работа трекера.
    """
    zone = (700.0, 500.0, 900.0, 700.0)
    data = scene({
        1: (0, straight(750.0, 600.0, 5.0, 20)),      # идёт внутри зоны
        2: (0, straight(100.0, 900.0, 5.0, 20)),      # далеко, мимо
    }, tail=2)
    assert track_stats(data).tracks == 2
    assert track_stats(data, zones=[zone]).tracks == 1


# --- из чего состоит поглощение -------------------------------------------
#
# «Поглощение» — след умер, и рядом никто не родился — внутри себя скрывает два
# разных механизма, и лечатся они противоположным:
#
# * **слияние** — детектор ВИДИТ человека, но отдал на него и на соседа одну
#   рамку. Это про подавление дубликатов в детекторе (NMS), а не про трекер;
# * **невидимость** — детектор не отдаёт на этом месте ничего: человека закрыл
#   борт или чужая спина. Это про узнавание по внешности при возвращении.
#
# Выбор лечения зависит от того, какого механизма больше. Различаются они по
# одному признаку: накрыто ли место исчезнувшего чужой рамкой на следующем
# кадре и выросла ли эта рамка.

def standing(x: float, y: float, n: int, half_width: float = 20.0
              ) -> list[tuple[float, float, float, float]]:
    return [(x - half_width, y - 120.0, x + half_width, y)] * n


def test_a_neighbour_box_that_grew_over_the_spot_is_a_merge():
    """Сосед раздулся ровно на место исчезнувшего — двое ушли в одну рамку."""
    data = boxes_scene({
        1: (0, standing(600.0, 600.0, 20)),
        2: (0, standing(640.0, 600.0, 20) + standing(620.0, 600.0, 20, 40.0)),
    }, tail=2)
    split = absorption_kinds(data)
    assert split.merged == 1
    assert split.invisible == 0 and split.occluded == 0


def test_an_empty_spot_after_the_death_is_invisibility():
    """На месте исчезнувшего нет ничьей рамки — детектор его не выдаёт вовсе."""
    data = boxes_scene({
        1: (0, standing(600.0, 600.0, 20)),
        2: (0, standing(1500.0, 300.0, 40)),      # далеко, места не накрывает
    }, tail=2)
    split = absorption_kinds(data)
    assert split.invisible == 1
    assert split.merged == 0


def test_a_neighbour_box_that_did_not_grow_is_not_a_merge():
    """Рамка соседа накрыла место, но не выросла — он просто заслонил собой.

    Это не слияние: детектор ничего не склеивал, человек ушёл за чужую спину.
    Лечится тем же, чем невидимость, — узнаванием по внешности, а не NMS.
    Записать такое в слияния значит пойти чинить детектор впустую.
    """
    data = boxes_scene({
        1: (0, standing(600.0, 600.0, 20)),
        2: (0, standing(600.0, 600.0, 40, 70.0)),   # широкий и НЕИЗМЕННЫЙ
    }, tail=2)
    split = absorption_kinds(data)
    assert split.occluded == 1
    assert split.merged == 0 and split.invisible == 0


def test_only_absorptions_are_split():
    """Передача личности и уход за край — не поглощения, их делить нечего."""
    handover = scene({
        1: (0, straight(600.0, 600.0, 10.0, 20)),
        2: (23, straight(800.0, 600.0, 10.0, 20)),
    }, tail=2)
    assert track_stats(handover).handovers == 1
    assert absorption_kinds(handover).total == 0

    border = scene({1: (0, straight(1700.0, 600.0, 15.0, 20))})
    assert track_stats(border).at_border == 1
    assert absorption_kinds(border).total == 0


def test_the_split_adds_up_to_the_number_of_absorptions():
    """Сумма трёх исходов обязана сойтись с числом поглощений.

    Иначе прибор молча теряет часть случаев, и доля механизма, по которой
    выбирается лечение, будет посчитана не от того знаменателя.
    """
    data = boxes_scene({
        1: (0, standing(600.0, 600.0, 20)),
        2: (0, standing(640.0, 600.0, 20) + standing(620.0, 600.0, 20, 40.0)),
        3: (0, standing(1500.0, 300.0, 20)),
    }, tail=2)
    assert absorption_kinds(data).total == track_stats(data).vanishings


def test_a_vanishing_inside_the_door_zone_is_told_apart():
    """Исчезнуть в дверной зоне — это, скорее всего, сесть в автобус.

    Человек, вошедший в салон, обязан пропасть с кадра: его исчезновение —
    искомое событие, а не дефект трекинга. Свалив его в одну кучу с потерей
    посреди тротуара, прибор объявит дефектом собственный сигнал и отправит
    чинить то, что работает.
    """
    zone = (700.0, 500.0, 900.0, 700.0)
    at_door = boxes_scene({
        1: (0, standing(800.0, 600.0, 20)),           # умер внутри зоны
        2: (0, standing(1500.0, 300.0, 40)),
    }, tail=2)
    assert absorption_kinds(at_door, zones=[zone]).in_zone == 1
    assert absorption_kinds(at_door, zones=[zone]).off_zone == 0

    walked_off = boxes_scene({
        1: (0, [(780.0, 480.0, 820.0, 600.0)] * 5
                + [(180.0, 480.0, 220.0, 600.0)] * 15),   # зону задел, умер вдали
        2: (0, standing(1500.0, 300.0, 40)),
    }, tail=2)
    assert absorption_kinds(walked_off, zones=[zone]).off_zone == 1
    assert absorption_kinds(walked_off, zones=[zone]).in_zone == 0


def test_where_and_how_split_the_same_absorptions():
    """Два разреза одного множества обязаны сойтись в сумме."""
    zone = (700.0, 500.0, 900.0, 700.0)
    data = boxes_scene({
        1: (0, standing(800.0, 600.0, 20)),
        2: (0, [(780.0, 480.0, 820.0, 600.0)] * 5
                + [(180.0, 480.0, 220.0, 600.0)] * 15),
        3: (0, standing(1500.0, 300.0, 40)),
    }, tail=2)
    split = absorption_kinds(data, zones=[zone])
    assert split.in_zone + split.off_zone == split.total
