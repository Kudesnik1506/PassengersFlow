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

Пороги соседства берутся у `zonecount` импортом, а не копией (запрет 9): «рядом
по времени и месту» здесь то же самое, что там, и разъехаться эти два понятия
не должны.
"""

from __future__ import annotations

import numpy as np
from paxcount.core.trackdata import FrameTracks, TrackData
from paxcount.trackstats import track_stats

FPS = 30.0
W, H = 1920, 1080


def scene(tracks: dict[int, tuple[int, list[tuple[float, float]]]],
           tail: int = 15) -> TrackData:
    """Сцена из следов: id → (кадр рождения, точки опоры по кадрам).

    Рамка человека строится вокруг точки опоры ростом 120 px — так же, как в
    `conftest.walk`, чтобы радиус соседства считался от той же величины.

    `tail` — сколько пустых кадров после последнего следа. По умолчанию 15
    (0.5 с при 30 к/с), то есть заведомо больше порога соседства: иначе любая
    смерть попадёт в «окно кончилось раньше человека». Тест про край окна
    задаёт хвост короче намеренно.
    """
    frames: list[FrameTracks] = []
    last = max(start + len(points) for start, points in tracks.values())
    for idx in range(last + tail):
        ids, boxes = [], []
        for tid, (start, points) in tracks.items():
            if start <= idx < start + len(points):
                x, y = points[idx - start]
                ids.append(tid)
                boxes.append((x - 20.0, y - 120.0, x + 20.0, y))
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
