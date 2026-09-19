"""Цепочка проездов по камере 1 и её сопоставление с остальными свидетелями.

Три камеры видят разное, и строку книги ни одна из них не даёт в одиночку.
К2 и К3 показывают, что машина ОСТАНОВИЛАСЬ, но не знают, какая это машина:
бортового номера детектор не читает (решение 003). К1 стоит в ста метрах вверх
по ходу и читает борт с подъезжающей морды, но стоянку от проезда не отличает
(решение 030). Оператор знает машину и время, но пропускает.

Здесь эти последовательности выравниваются между собой. Выравнивание, а не
поиск ближайшего по времени: машины идут раз в 80–120 секунд, часы камер
разъезжаются на минуты, и «ближайший» на такой дистанции — уже соседняя
машина. Заказчик назвал главным признаком порядок прибытия (решение 028), и
порядок здесь — не следствие, а условие: индексы в паре не убывают никогда.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING, Mapping, Sequence

if TYPE_CHECKING:  # трекинг тянет numpy и веса — цепочке они не нужны
    from ..visits import VehicleTrack
    from .visibility import Sighting

# Цена пропуска с любой стороны. Пара оценивается от 0 до 1, поэтому пропуск
# дороже самой слабой пары: выравнивание предпочтёт спарить далёкое, но
# возможное, и оставит пропуск там, где пары нет вовсе.
GAP_COST = -1.0

# Сколько пар нужно, чтобы говорить о сдвиге файла. Сдвиг подгоняется по тем же
# звеньям, которые потом ищутся этим сдвигом, — на двух парах подгонка
# объясняет сама себя и уводит ленту правдоподобно.
MIN_PAIRS = 3


@dataclass(frozen=True)
class Passage:
    """Проезд мимо камеры: прохождение, а не стоянка (решение 030).

    `peak` — момент кадра, по которому машину опознают: тот, где рамка крупнее
    всего (решение 028). Время — на часах своей камеры, как и в `Sighting`.
    """

    camera: str
    file: str
    track: int
    start: datetime
    end: datetime
    peak: datetime
    box: tuple[float, float, float, float]
    frame_size: tuple[int, int]


def align(left: Sequence[datetime],
           right: Sequence[datetime],
           *,
           shift: timedelta,
           tolerance: timedelta,
           anchors: Mapping[int, int] | None = None,
           ) -> list[tuple[int | None, int | None]]:
    """Две последовательности моментов, спаренные по порядку и времени.

    `shift` — ожидаемое смещение правой шкалы относительно левой, `tolerance` —
    сколько сверх него прощается. За допуском пары нет вовсе: далёкий сосед не
    становится парой оттого, что других нет.

    `anchors` — пары, уже известные из прямого свидетельства (бортовой номер,
    решение 068). Время их не переставляет: косвенный признак не спорит с
    прямым.

    `None` с любой стороны — пропуск, и это законный исход: обе стороны
    пропускают машины, ради чего всё и затевается.
    """
    anchors = dict(anchors or {})
    held_right = set(anchors.values())
    n, m = len(left), len(right)
    seconds = tolerance.total_seconds()

    def score(i: int, j: int) -> float | None:
        if i in anchors:
            return 1.0 if anchors[i] == j else None
        if j in held_right:
            return None
        off = abs((left[i] - right[j] - shift).total_seconds())
        return 1.0 - off / seconds if off <= seconds else None

    best: list[list[float]] = [[0.0] * (m + 1) for _ in range(n + 1)]
    step: list[list[str]] = [[""] * (m + 1) for _ in range(n + 1)]
    for i in range(1, n + 1):
        best[i][0] = best[i - 1][0] + GAP_COST
        step[i][0] = "left"
    for j in range(1, m + 1):
        best[0][j] = best[0][j - 1] + GAP_COST
        step[0][j] = "right"

    for i in range(1, n + 1):
        for j in range(1, m + 1):
            ways: list[tuple[float, str]] = []
            pair = score(i - 1, j - 1)
            if pair is not None:
                ways.append((best[i - 1][j - 1] + pair, "pair"))
            if i - 1 not in anchors:
                ways.append((best[i - 1][j] + GAP_COST, "left"))
            if j - 1 not in held_right:
                ways.append((best[i][j - 1] + GAP_COST, "right"))
            if not ways:                      # якорь не даёт пройти иначе
                ways.append((best[i - 1][j - 1] + GAP_COST * 2, "pair"))
            best[i][j], step[i][j] = max(ways)

    rows: list[tuple[int | None, int | None]] = []
    i, j = n, m
    while i or j:
        move = step[i][j]
        if move == "pair":
            i, j = i - 1, j - 1
            rows.append((i, j))
        elif move == "left":
            i -= 1
            rows.append((i, None))
        else:
            j -= 1
            rows.append((None, j))
    rows.reverse()
    return rows


def fitted_shift(left: Sequence[datetime],
                  right: Sequence[datetime],
                  rows: Sequence[tuple[int | None, int | None]],
                  minimum: int | None = None) -> timedelta:
    """Смещение шкал по спаренным звеньям — медианой, а не средним.

    Тем же приёмом меряется расхождение часов оператора (`sequence.clock_shift`):
    одно испорченное звено не вправе утащить ленту за собой.

    `minimum` — сколько пар считать замером. Меньше — отказ: по двум парам
    сдвиг не измеряется, а придумывается.
    """
    offsets = [left[i] - right[j] for i, j in rows if i is not None and j is not None]
    if minimum is not None and len(offsets) < minimum:
        raise ValueError(
            f"пар для замера сдвига {len(offsets)}, нужно {minimum}: "
            "по такому числу сдвиг не измеряется, а придумывается"
        )
    if not offsets:
        raise ValueError("пар нет вовсе — сдвиг мерить не по чему")
    return timedelta(seconds=statistics.median(o.total_seconds() for o in offsets))


# Пороги проезда. Числа взяты замером по первому файлу К1 (25 минут): при
# ширине рамки от 600 px и длительности от 5 с набирается 17 звеньев, и они
# сходятся с 12 нажатиями оператора без единой лишней пары. Ниже порога идёт
# поток через перекрёсток — там номер не прочитать, а в цепочке он даст
# звенья, которых у остановки не было.
MIN_WIDTH_PX = 600.0
MIN_PASSAGE_S = 5.0

# Насколько рамка должна отступить от края, чтобы кадр считался целым. Тот же
# допуск, что у обреза кузова в `reconcile`: край кадра дрожит на пиксель.
EDGE_PX = 2.0


def passages(tracks: Mapping[int, "VehicleTrack"],
              *,
              camera: str,
              file: str,
              start: datetime,
              frame_size: tuple[int, int],
              min_width: float = MIN_WIDTH_PX,
              min_seconds: float = MIN_PASSAGE_S,
              ) -> list[Passage]:
    """Проезды по трекам одного файла, в порядке прохождения.

    `start` — начало записи по имени файла: время трека идёт от него.

    Кадр опознания ищется среди тех, где рамка НЕ задевает край: на пике
    проезда морда с номером уже наполовину за кадром, и рамка там шире всего
    именно поэтому. Целых кадров нет вовсе — берём крупнейший из обрезанных и
    отдаём как есть: пусть решает тот, кто будет читать.
    """
    width, height = frame_size
    found: list[Passage] = []
    for track_id, track in sorted(tracks.items()):
        if not track.boxes:
            continue
        if track.times[-1] - track.times[0] < min_seconds:
            continue
        if max(float(b[2] - b[0]) for b in track.boxes) < min_width:
            continue
        whole = [n for n, b in enumerate(track.boxes)
                  if b[0] > EDGE_PX and b[1] > EDGE_PX
                  and b[2] < width - EDGE_PX and b[3] < height - EDGE_PX]
        among = whole or range(len(track.boxes))
        peak = max(among, key=lambda n: float(
            (track.boxes[n][2] - track.boxes[n][0])
            * (track.boxes[n][3] - track.boxes[n][1])))
        box = track.boxes[peak]
        found.append(Passage(
            camera=camera, file=file, track=track_id,
            start=start + timedelta(seconds=track.times[0]),
            end=start + timedelta(seconds=track.times[-1]),
            peak=start + timedelta(seconds=track.times[peak]),
            box=(float(box[0]), float(box[1]), float(box[2]), float(box[3])),
            frame_size=frame_size,
        ))
    return sorted(found, key=lambda p: p.start)


# Ширина рамки, ниже которой проезд идёт по дальней полосе. Замер по утренней
# смене: у 100 проездов, подтверждённых нажатием оператора, ширина от 900 px
# (медиана 1158), а встречный троллейбус на дальней полосе даёт около 600.
# Порог отсекает 23 ничьих проезда из 47, теряя 4 подтверждённых из 100.
MIN_CANDIDATE_PX = 900.0


@dataclass(frozen=True)
class Candidate:
    """Машина, которой нет ни у оператора, ни у нас, — с двумя свидетельствами.

    `passage` говорит, КТО это (с кадра читается борт), `sighting` — что она
    ОСТАНОВИЛАСЬ. По отдельности ни того ни другого мало: К1 снимает и тех, кто
    остановку минует, а стоянка сама по себе безымянна (решение 003).

    `sighting is None` — стоянку не показала ни одна счётная камера, но и
    смотреть в этот момент было нечем: обе молчали. Такая строка заводится с
    оговоркой в `note`, и оговорка эта едет в книгу словами.
    """

    passage: Passage
    sighting: "Sighting | None"
    note: str = ""


NO_STOP_NOTE = "стоянка не подтверждена: счётные камеры в этот момент молчали"


def candidates(orphans: Sequence[Passage],
                free: Sequence["Sighting"],
                *,
                shift: timedelta,
                window: timedelta,
                blind: Sequence[tuple[datetime, datetime]] = (),
                min_width: float = MIN_CANDIDATE_PX,
                ) -> list[Candidate]:
    """Пропущенные машины: ничей проезд рядом с ничьей стоянкой.

    `shift` — «часы проезда минус часы стоянки», `window` — насколько они
    расходятся сверх него. `free` — стоянки ЛЮБОЙ счётной камеры, у которых нет
    своей строки, уже приведённые к шкале проезда.

    `blind` — окна, где счётные камеры не показали ни одной стоянки вовсе.
    Там подтверждения требовать не с чего: таблица стоянок К2 неполна (42 файла
    из 48, и шесть минут подряд без единого визита при идущей записи), и
    требование подтверждения теряло бы машины именно там, где смотреть нечем.

    Стоянке достаётся ОДИН проезд, ближайший по времени: разорванный трек даёт
    два проезда на одну машину, и обе строки ушли бы в книгу как два заезда.
    """
    близкие = [p for p in orphans if p.box[2] - p.box[0] >= min_width]
    found: list[Candidate] = []
    for stop in sorted(free, key=lambda s: s.start):
        подходят = [p for p in близкие
                     if abs((p.peak - stop.start - shift).total_seconds())
                     <= window.total_seconds()]
        if not подходят:
            continue
        ближайший = min(подходят,
                         key=lambda p: abs((p.peak - stop.start - shift).total_seconds()))
        близкие = [p for p in близкие if p is not ближайший]
        found.append(Candidate(passage=ближайший, sighting=stop))

    for passage in близкие:                  # остались без стоянки
        if any(start <= passage.peak <= end for start, end in blind):
            found.append(Candidate(passage=passage, sighting=None, note=NO_STOP_NOTE))
    return _one_per_machine(sorted(found, key=lambda c: c.passage.peak))


# Два кандидата в один и тот же момент — это один кузов, увиденный дважды:
# трекер разрывает его, когда машину заслоняет другая (боевой кадр: автобус
# наполовину закрыт грузовым фургоном, два проезда в одну секунду).
#
# Порог именно такой узкий. Взятый шире (45 с), он съел настоящую машину:
# два автобуса встали в карман с разницей в 25 секунд, и маршрут 26 пропал
# вместе со строкой.
SAME_MACHINE_S = 10.0


def _one_per_machine(found: list[Candidate]) -> list[Candidate]:
    """Оставляет по одному кандидату на машину. Две строки на одну — брак."""
    kept: list[Candidate] = []
    for candidate in found:
        if kept and (candidate.passage.peak - kept[-1].passage.peak
                      ).total_seconds() < SAME_MACHINE_S:
            continue
        kept.append(candidate)
    return kept


def blind_windows(stops: Sequence[datetime],
                   *,
                   span: timedelta,
                   ) -> list[tuple[datetime, datetime]]:
    """Промежутки между стоянками длиннее `span` — там камеры ничего не дали.

    Машины идут через 80–120 секунд, поэтому пустота в несколько минут это не
    затишье на остановке, а слепота счёта.
    """
    порядок = sorted(stops)
    return [(a, b) for a, b in zip(порядок, порядок[1:])
             if (b - a) >= span]


def already_known(moment: datetime,
                   number: str,
                   book: Sequence[tuple[datetime, str]],
                   *,
                   window: timedelta,
                   ) -> bool:
    """Есть ли в книге строка на ЭТОТ ЖЕ заезд этой же машины.

    Последняя проверка перед тем, как завести строку. Выравнивание идёт по
    времени и ошибается: лишнее звено сдвигает разбор, и проезд остаётся
    ничьим, хотя оператор машину записал. На боевом утре так вышло у четырёх
    кандидатов из двенадцати, и все четыре ушли бы в книгу вторыми строками на
    тот же заезд — ровно то, за что заказчик бракует файл (решение 075).

    Совпадения одного номера мало: машина ходит по кругу и за сутки
    возвращается до пяти раз. Поэтому сверяется пара «номер и время».

    Без номера проверить нечего, и выдавать непроверенное за проверенное
    нельзя: такая строка заводится под ответственность заказчика.
    """
    if not number:
        return False
    край = window.total_seconds()
    return any(known == number and abs((moment - at).total_seconds()) <= край
                for at, known in book)
