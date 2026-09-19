"""Проезды камеры 1, собранные из треков детекции.

`sightings` собирает СТОЯНКИ и для К1 бесполезен: по первому файлу детектор
нашёл там три визита за двадцать пять минут — машины мимо К1 едут, а не стоят
(решение 030). Поэтому цепочка строится из треков транспорта напрямую.

Кадр, по которому машину будут опознавать, выбирается по треку — тот, где
рамка крупнее всего (решение 028). Здесь покадровая рамка уместна вопреки
общему правилу (запрет 3): она не участвует в счёте, а называет кадр.
"""

from __future__ import annotations

from datetime import datetime

import numpy as np

from paxcount.delivery.chain import MIN_PASSAGE_S, MIN_WIDTH_PX, passages
from paxcount.visits import VehicleTrack

START = datetime(2026, 9, 10, 6, 56, 30)
FRAME = (1920, 1080)


def track(track_id: int, boxes: list[tuple[float, float, float, float]],
           *, step: float = 1.0, first: float = 0.0) -> VehicleTrack:
    made = VehicleTrack(track_id=track_id)
    for n, box in enumerate(boxes):
        made.add(n, first + n * step, np.array(box, dtype=float), "bus")
    return made


def wide(x0: float, width: float) -> tuple[float, float, float, float]:
    return (x0, 200.0, x0 + width, 900.0)


def test_a_short_fragment_is_not_a_passage():
    """Обрывок трека проездом не считается: опознавать там нечего."""
    short = track(1, [wide(400, 200)] * 2)
    assert passages({1: short}, camera="1", file="ф", start=START,
                     frame_size=FRAME) == []


def test_a_distant_vehicle_is_not_a_passage():
    """Машина на дальней полосе идёт мимо остановки, а не к ней.

    Порог по ширине рамки отсекает поток через перекрёсток: номер там не
    прочитать, а в цепочку он добавил бы звенья, которых на остановке не было.
    """
    far = track(2, [wide(300, MIN_WIDTH_PX - 50)] * 20)
    assert passages({2: far}, camera="1", file="ф", start=START,
                     frame_size=FRAME) == []


def test_the_peak_frame_avoids_a_clipped_box():
    """Кадр опознания — крупнейший СРЕДИ НЕ ЗАДЕВАЮЩИХ край.

    На пике проезда морда с бортовым номером уже наполовину за кадром:
    рамка там шире всего именно потому, что кузов обрезан.
    """
    seen = track(3, [wide(400, 700), wide(300, 900), wide(0, 1500)], step=5.0)
    found = passages({3: seen}, camera="1", file="ф", start=START,
                      frame_size=FRAME)
    assert len(found) == 1
    assert found[0].peak == START.replace(second=35)      # второй кадр, не третий
    assert found[0].box[0] > 0, "рамка выбранного кадра края не касается"


def test_the_peak_is_the_biggest_box_when_nothing_is_clipped():
    seen = track(4, [wide(400, 700), wide(350, 1000), wide(500, 800)], step=5.0)
    found = passages({4: seen}, camera="1", file="ф", start=START,
                      frame_size=FRAME)
    assert found[0].peak == START.replace(second=35)


def test_a_passage_keeps_where_it_came_from():
    """Файл и трек — обратный путь к кадру: по ним его достанут заново."""
    seen = track(5, [wide(400, 800)] * int(MIN_PASSAGE_S + 2))
    found = passages({5: seen}, camera="1", file="запись", start=START,
                      frame_size=FRAME)
    assert (found[0].camera, found[0].file, found[0].track) == ("1", "запись", 5)
    assert found[0].start == START and found[0].frame_size == FRAME
