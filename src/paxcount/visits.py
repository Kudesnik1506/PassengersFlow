"""Автодетект визитов ТС: приехал → стоит → уехал.

Решение №5 в плане: визит определяется по стационарности трека ТС. Скорость
считается как смещение центра bbox, нормированное на диагональ этого bbox —
так порог не зависит от того, близко машина к камере или далеко.

Отдельно от смещения центра проверяется устойчивость РАЗМЕРА bbox: смещение
центра слепо к движению ТС прямо на/от камеры (диагональ растёт или падает,
а центр остаётся на месте) — см. `_size_ratio` и стабилизацию координат ниже.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

from .core.trackdata import TrackData
from .core.types import VehicleVisit, VideoConfig

# Сколько держим последний bbox ТС, если детектор его потерял.
VEHICLE_MEMORY_SECONDS = 0.5

# Доля кадров с каждого края bbox, отсекаемая при вычислении канонического
# bbox визита/окна: x0/y0 берутся из нижнего квантиля, x1/y1 — из верхнего.
#
# ВАЖНО: значение 0.0 (честный минимум/максимум по окну), а не "мягкий"
# квантиль вроде 0.15. Причина не в стабильности — устойчивость к подвыборке
# (чёт/нечет кадров внутри окна) у 0.0 и у 0.15 практически ОДИНАКОВАЯ (замер
# на видео 01: среднее расхождение 4.9px против 4.5px, максимум 25px против
# 22px) — то есть отсечение "выбросов" квантилем 0.15 не даёт заявленной
# защиты от шума. Зато оно систематически ХУЖЕ по точности: детектор ТОЛЬКО
# обрезает объект (никогда не раздувает), поэтому самое щедрое из наблюдений
# в окне — не выброс, а самая правдивая оценка истинной границы. С q=0.15
# зона двери в хвостовой секции сочленённого автобуса (01, door1/door2)
# оставалась заметно уже видимого кузова даже после стабилизации — ровно та
# картина, на которую указал заказчик, глядя на итоговый рендер.
CANONICAL_Q = 0.0

# Длина окна, на которое режется стационарный визит перед тем как считать
# канонический bbox. ТС может реально покачиваться на подвеске при посадке/
# высадке (замерено ORB+RANSAC на видео 01: ~110px за 67с, некогерентно с
# шумом детектора) — один bbox на весь визит промахивается на краях длинного
# визита. 5с — компромисс между остаточным дрожанием внутри окна и числом
# кадров, достаточным для устойчивой квантильной оценки.
CANONICAL_WINDOW_SECONDS = 5.0

# Минимум реальных детекций ТС внутри окна/визита, при котором канонический
# bbox вообще считается — иначе квантиль по единицам точек хуже сырого бокса.
MIN_CANONICAL_SAMPLES = 8


@dataclass
class VehicleTrack:
    track_id: int
    frames: list[int] = field(default_factory=list)
    times: list[float] = field(default_factory=list)
    boxes: list[np.ndarray] = field(default_factory=list)

    def add(self, frame_idx: int, ts: float, box: np.ndarray) -> None:
        self.frames.append(frame_idx)
        self.times.append(ts)
        self.boxes.append(box)

    def area(self) -> float:
        if not self.boxes:
            return 0.0
        b = self.boxes[-1]
        return float((b[2] - b[0]) * (b[3] - b[1]))


def _normalised_speed(boxes: list[np.ndarray], window: int = 1) -> np.ndarray:
    """Скорость центра в долях диагонали bbox за кадр.

    Смещение берётся не между соседними кадрами, а через окно: покадровая
    разница у стоящего ТС состоит почти целиком из дрожания рамки детектора и
    даёт ложные «поехал». Окно усредняет это дрожание, сохраняя реальный старт.
    """
    if len(boxes) < 2:
        return np.zeros(len(boxes))
    arr = np.asarray(boxes, dtype=float)
    cx = (arr[:, 0] + arr[:, 2]) / 2
    cy = (arr[:, 1] + arr[:, 3]) / 2
    diag = np.hypot(arr[:, 2] - arr[:, 0], arr[:, 3] - arr[:, 1])
    diag[diag == 0] = 1.0
    w = max(1, min(window, len(boxes) - 1))
    idx = np.arange(len(boxes))
    prev = np.maximum(idx - w, 0)
    span = np.maximum(idx - prev, 1)
    d = np.hypot(cx - cx[prev], cy - cy[prev]) / (diag * span)
    return d


def _size_ratio(boxes: list[np.ndarray]) -> float:
    """Робастная устойчивость размера bbox: p90/p10 диагонали.

    Смещение центра, нормированное на диагональ (см. `_normalised_speed`),
    слепо к движению ТС прямо на камеру: центр стоит, диагональ растёт.
    Замер на реальных роликах: ТС, едущее на камеру, даёт p90/p10 ~3.4;
    настоящая стоянка — 1.0-1.1 даже с дрожанием детектора.
    """
    arr = np.asarray(boxes, dtype=float)
    diag = np.hypot(arr[:, 2] - arr[:, 0], arr[:, 3] - arr[:, 1])
    lo = np.percentile(diag, 10)
    if lo <= 0:
        return float("inf")
    return float(np.percentile(diag, 90) / lo)


def _smooth(x: np.ndarray, window: int) -> np.ndarray:
    if window <= 1 or len(x) < window:
        return x
    kernel = np.ones(window) / window
    return np.convolve(x, kernel, mode="same")


def detect_visits(
    track: VehicleTrack,
    video: str,
    fps: float,
    stationary_speed: float = 0.010,
    min_visit_seconds: float = 1.5,
    smooth_seconds: float = 0.5,
    stationary_scale: float = 1.25,
) -> list[VehicleVisit]:
    """Режет трек ТС на визиты — участки, где машина стоит.

    Кандидат-визит дополнительно проверяется на устойчивость РАЗМЕРА bbox
    (`_size_ratio`): движение центра слепо к ТС, едущему прямо на камеру,
    а такой кандидат — не стоянка. Непрошедший проверку визит помечается
    `stationary=False`, а не отбрасывается — счёт по нему всё ещё идёт (это
    просто отменяет каноническую стабилизацию bbox для него), но границы
    визита не рвутся напрасно.
    """
    if len(track.boxes) < 2:
        return []

    window = max(1, int(fps * smooth_seconds))
    speed = _smooth(_normalised_speed(track.boxes, window=window), window)
    stationary = speed < stationary_speed

    visits: list[VehicleVisit] = []
    start: int | None = None
    min_frames = max(1, int(fps * min_visit_seconds))

    def close(i0: int, i1: int) -> None:
        if i1 - i0 + 1 >= min_frames:
            ratio = _size_ratio(track.boxes[i0 : i1 + 1])
            visits.append(
                _make_visit(track, video, i0, i1, len(visits) + 1, ratio <= stationary_scale)
            )

    for i, is_stationary in enumerate(stationary):
        if is_stationary and start is None:
            start = i
        elif not is_stationary and start is not None:
            close(start, i - 1)
            start = None
    if start is not None:
        close(start, len(stationary) - 1)
    return visits


def _make_visit(
    track: VehicleTrack, video: str, i0: int, i1: int, visit_id: int, stationary: bool
) -> VehicleVisit:
    return VehicleVisit(
        video=video,
        visit_id=visit_id,
        vehicle_track_id=track.track_id,
        arrival_ts=round(track.times[i0], 2),
        departure_ts=round(track.times[i1], 2),
        stationary=stationary,
    )


def pick_primary(tracks: dict[int, VehicleTrack]) -> VehicleTrack | None:
    """Главное ТС сцены — с наибольшей суммарной площадью присутствия.

    Отсекает автобусы на заднем плане: они и мельче, и в кадре меньше времени.
    """
    if not tracks:
        return None
    def weight(t: VehicleTrack) -> float:
        if not t.boxes:
            return 0.0
        arr = np.asarray(t.boxes, dtype=float)
        areas = (arr[:, 2] - arr[:, 0]) * (arr[:, 3] - arr[:, 1])
        return float(areas.sum())
    return max(tracks.values(), key=weight)


def vehicle_tracks(data: TrackData) -> dict[int, VehicleTrack]:
    tracks: dict[int, VehicleTrack] = {}
    for f in data.frames:
        for tid, box in zip(f.vehicle_ids, f.vehicle_boxes):
            tracks.setdefault(int(tid), VehicleTrack(int(tid))).add(f.frame_idx, f.ts, box)
    return tracks


def track_boxes(
    track: VehicleTrack | None, data: TrackData, fps: float
) -> dict[int, np.ndarray]:
    """Сырой bbox одного ТС по кадрам, с удержанием последнего при потере детекции.

    Это нижний, "сырой" слой. Для счёта берите `Scene.boxes_for(visit)`, для
    разметки в веб-UI — `Scene.boxes`: там bbox стабилизирован на стационарных
    визитах и не дрожит на порядки сильнее самих дверных зон.
    """
    if track is None:
        return {}
    known = dict(zip(track.frames, track.boxes))
    memory = max(1, int(round(VEHICLE_MEMORY_SECONDS * fps))) * data.stride
    out: dict[int, np.ndarray] = {}
    last_box: np.ndarray | None = None
    last_frame = -10**9
    for f in data.frames:
        if f.frame_idx in known:
            last_box, last_frame = known[f.frame_idx], f.frame_idx
        if last_box is not None and f.frame_idx - last_frame <= memory:
            out[f.frame_idx] = last_box
    return out


def canonical_box(
    boxes: list[np.ndarray], q: float = CANONICAL_Q
) -> np.ndarray | None:
    """Один устойчивый bbox по набору сырых кадровых bbox.

    Края берутся квантилями НАРУЖУ (x0/y0 — нижний квантиль, x1/y1 — верхний):
    детектор систематически обрезает объект, а не раздувает его, поэтому
    распределение краёв скошено и правда — у щедрого хвоста, не у медианы.
    `None`, если реальных наблюдений меньше `MIN_CANONICAL_SAMPLES` — квантиль
    по единицам точек хуже, чем просто отдать сырой bbox без стабилизации.
    """
    if len(boxes) < MIN_CANONICAL_SAMPLES:
        return None
    arr = np.asarray(boxes, dtype=float)
    x0 = np.percentile(arr[:, 0], 100 * q)
    y0 = np.percentile(arr[:, 1], 100 * q)
    x1 = np.percentile(arr[:, 2], 100 * (1 - q))
    y1 = np.percentile(arr[:, 3], 100 * (1 - q))
    return np.array([x0, y0, x1, y1], dtype=float)


def canonical_windows(
    track: VehicleTrack,
    visit: VehicleVisit,
    window_seconds: float = CANONICAL_WINDOW_SECONDS,
    q: float = CANONICAL_Q,
) -> list[tuple[float, float, np.ndarray]]:
    """Режет визит на окна фиксированной длины, считает канонический bbox в каждом.

    Возвращает список (t0, t1, box) по времени трека. ТС может реально
    покачиваться на подвеске в течение долгой стоянки — один bbox на весь
    визит промахивается на краях (см. плановый документ), поэтому bbox
    пересчитывается заново каждые `window_seconds`.
    """
    times = np.asarray(track.times, dtype=float)
    mask = (times >= visit.arrival_ts) & (times <= visit.departure_ts)
    idx = np.nonzero(mask)[0]
    if len(idx) == 0:
        return []
    windows: list[tuple[float, float, np.ndarray]] = []
    t0 = visit.arrival_ts
    while t0 < visit.departure_ts:
        t1 = min(t0 + window_seconds, visit.departure_ts)
        sel = [track.boxes[i] for i in idx if t0 <= times[i] <= t1]
        box = canonical_box(sel, q)
        if box is not None:
            windows.append((t0, t1, box))
        t0 = t1
    return windows


@dataclass(frozen=True)
class Scene:
    """Единственный источник bbox ТС для счёта и для разметки в веб-UI.

    На ролике может стоять НЕ ОДНО ТС: за час на остановке их десятки, и два
    могут стоять одновременно. Поэтому bbox по кадрам хранится отдельно для
    каждого визита (`visit_boxes`), а не одной картой кадр→bbox: при двух
    одновременных ТС такая карта физически не может представить оба.

    - `boxes_for(visit)` — то, чем считает бэкенд: канонический bbox внутри
      стационарного визита (стабилен на коротких окнах), сырой с удержанием —
      в остальных кадрах визита.
    - `boxes` — та же величина, но для главного ТС сцены; нужна разметке в
      веб-UI и doorprop, которым надо показать один кадр и один кузов.
    - `raw_boxes` и `visits` — для диагностики и метрик дрожания.
    """

    primary: VehicleTrack | None
    visits: list[VehicleVisit]
    boxes: dict[int, np.ndarray]
    raw_boxes: dict[int, np.ndarray]
    visit_boxes: dict[int, dict[int, np.ndarray]] = field(default_factory=dict)
    raw_visit_boxes: dict[int, dict[int, np.ndarray]] = field(default_factory=dict)

    def boxes_for(self, visit: VehicleVisit) -> dict[int, np.ndarray]:
        """bbox по кадрам для конкретного визита.

        Фолбэк на `boxes` — для эрзац-визита, который бэкенд создаёт сам,
        когда детект визитов не нашёл ни одного (его нет в `visit_boxes`).
        """
        return self.visit_boxes.get(visit.visit_id, self.boxes)

    def raw_for(self, visit: VehicleVisit) -> dict[int, np.ndarray]:
        """Сырой (нестабилизированный) bbox ТС этого визита — для диагностики."""
        return self.raw_visit_boxes.get(visit.visit_id, self.raw_boxes)


def area_share(track: VehicleTrack, frame_area: float) -> float:
    """Какую долю кадра занимает это ТС (медиана по его кадрам).

    Отсев фоновых ТС: автобус на соседней улице тоже даёт трек и тоже иногда
    стоит, то есть формально даёт визит. Дверные зоны, применённые к нему,
    ловили бы прохожих на заднем плане. Медиана, а не среднее: ТС, въезжающее
    в кадр, начинает с крошечного bbox, и среднее занижало бы его размер.
    """
    if not track.boxes or frame_area <= 0:
        return 0.0
    arr = np.asarray(track.boxes, dtype=float)
    areas = (arr[:, 2] - arr[:, 0]) * (arr[:, 3] - arr[:, 1])
    return float(np.median(areas) / frame_area)


def visit_boxes(
    track: VehicleTrack,
    visit: VehicleVisit,
    data: TrackData,
    config: VideoConfig,
    fps: float,
    raw: dict[int, np.ndarray] | None = None,
) -> dict[int, np.ndarray]:
    """bbox по кадрам одного визита: канонический на стоянке, сырой иначе."""
    raw = track_boxes(track, data, fps) if raw is None else raw
    windows = (
        canonical_windows(track, visit, config.canonical_window_seconds)
        if visit.stationary
        else []
    )
    out: dict[int, np.ndarray] = {}
    for f in data.frames:
        if not (visit.arrival_ts <= f.ts <= visit.departure_ts):
            continue
        box = raw.get(f.frame_idx)
        if box is None:
            continue
        # Окна кладутся по времени КАДРА, а не по индексам детекций трека —
        # иначе кадры, где bbox лишь удержан "памятью" (см. track_boxes),
        # остались бы на сыром боксе внутри уже стабилизированного окна.
        for t0, t1, cbox in windows:
            if t0 <= f.ts <= t1:
                box = cbox
                break
        out[f.frame_idx] = box
    return out


def _overlap_min(a: np.ndarray, b: np.ndarray) -> float:
    """Пересечение, делённое на площадь МЕНЬШЕЙ рамки.

    Не IoU: у одного и того же ТС, схваченного двумя треками, рамки различаются
    именно обрезкой (детектор систематически обрезает объект, а не раздувает
    его), и IoU из-за этого проседает. Замер на видео 04, где обе рамки лежат
    на одном поезде: IoU 0.45 — ниже любого разумного порога, тогда как это
    отношение даёт 0.78. Для двух РАЗНЫХ машин у одной остановки оно близко к
    нулю: они занимают разные места кадра.
    """
    x0, y0 = max(a[0], b[0]), max(a[1], b[1])
    x1, y1 = min(a[2], b[2]), min(a[3], b[3])
    inter = max(x1 - x0, 0.0) * max(y1 - y0, 0.0)
    if inter <= 0:
        return 0.0
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return float(inter / max(min(area_a, area_b), 1.0))


def _area_ratio(a: np.ndarray, b: np.ndarray) -> float:
    area_a = (a[2] - a[0]) * (a[3] - a[1])
    area_b = (b[2] - b[0]) * (b[3] - b[1])
    return float(min(area_a, area_b) / max(max(area_a, area_b), 1.0))


def merge_fragments(
    found: list[tuple[VehicleTrack, VehicleVisit]],
    raw_by_track: dict[int, dict[int, np.ndarray]],
    min_overlap: float = 0.6,
    min_area_ratio: float = 0.5,
) -> list[tuple[VehicleTrack, VehicleVisit]]:
    """Склеивает визиты, которые на деле принадлежат одному ТС.

    Трекер теряет и заново заводит id (перекрытие, смена ракурса) — тогда одна
    физическая машина даёт два трека, пересекающихся во времени и стоящих в
    одном месте кадра. Без склейки оба визита считают ОДНИХ И ТЕХ ЖЕ людей:
    на видео 04 это дало дубли по трекам 349/305/377 — человек, посчитанный
    дважды.

    Признак одного ТС — совпадение во времени, высокое `_overlap_min` И
    сопоставимая площадь рамок. Условие про площадь обязательно: без него
    легковушка, целиком попавшая в рамку автобуса, слилась бы с ним — её
    `_overlap_min` равен единице, а отношение площадей мало́.
    """
    kept: list[tuple[VehicleTrack, VehicleVisit]] = []
    for track, visit in found:
        merged = False
        for i, (ktrack, kvisit) in enumerate(kept):
            if visit.arrival_ts > kvisit.departure_ts or visit.departure_ts < kvisit.arrival_ts:
                continue
            shared = set(raw_by_track[track.track_id]) & set(raw_by_track[ktrack.track_id])
            if not shared:
                continue
            mine, theirs = raw_by_track[track.track_id], raw_by_track[ktrack.track_id]
            overlaps = [_overlap_min(mine[f], theirs[f]) for f in shared]
            ratios = [_area_ratio(mine[f], theirs[f]) for f in shared]
            if (
                float(np.median(overlaps)) < min_overlap
                or float(np.median(ratios)) < min_area_ratio
            ):
                continue
            # Базой остаётся более длинный трек: его bbox устойчивее, а
            # канонические окна считаются именно по треку.
            base_track, base_visit = (
                (ktrack, kvisit) if len(ktrack.frames) >= len(track.frames) else (track, visit)
            )
            kept[i] = (
                base_track,
                base_visit.model_copy(update={
                    "arrival_ts": min(visit.arrival_ts, kvisit.arrival_ts),
                    "departure_ts": max(visit.departure_ts, kvisit.departure_ts),
                    "stationary": visit.stationary and kvisit.stationary,
                }),
            )
            merged = True
            break
        if not merged:
            kept.append((track, visit))
    return kept


def build_scene(data: TrackData, config: VideoConfig) -> Scene:
    """Собирает треки ТС, визиты и стабилизированные bbox — единая точка входа.

    Визиты ищутся по КАЖДОМУ достаточно крупному треку ТС, а не только по
    главному: на многочасовой записи к остановке подъезжают десятки машин, и
    выбор одной "главной" (наибольшей суммарной площади) означал бы, что
    посчитан ровно один автобус, а остальные молча пропущены. Главное ТС
    остаётся — но только как то, что показывается в разметке.
    """
    tracks = vehicle_tracks(data)
    primary = pick_primary(tracks)
    if primary is None:
        return Scene(primary=None, visits=[], boxes={}, raw_boxes={}, visit_boxes={})

    fps = data.effective_fps
    frame_area = float(data.width * data.height)
    raw_by_track = {
        tid: track_boxes(t, data, fps)
        for tid, t in tracks.items()
        if area_share(t, frame_area) >= config.min_vehicle_area
    }
    found: list[tuple[VehicleTrack, VehicleVisit]] = []
    for tid, raw in raw_by_track.items():
        track = tracks[tid]
        found.extend(
            (track, v)
            for v in detect_visits(
                track,
                config.video,
                fps,
                config.stationary_speed,
                config.min_visit_seconds,
                stationary_scale=config.stationary_scale,
            )
        )
    # Нумерация визитов сквозная по времени прибытия: id визита попадает в
    # отчёт, и «визит 3» должен значить третий по счёту приехавший, а не
    # третий у случайно первого в словаре трека.
    found.sort(key=lambda tv: (tv[1].arrival_ts, tv[0].track_id))
    found = merge_fragments(found, raw_by_track)
    visits: list[VehicleVisit] = []
    per_visit: dict[int, dict[int, np.ndarray]] = {}
    raw_per_visit: dict[int, dict[int, np.ndarray]] = {}
    for i, (track, v) in enumerate(found, 1):
        v = v.model_copy(update={"visit_id": i})
        visits.append(v)
        raw = raw_by_track[track.track_id]
        per_visit[i] = visit_boxes(track, v, data, config, fps, raw)
        raw_per_visit[i] = {
            f.frame_idx: raw[f.frame_idx]
            for f in data.frames
            if v.arrival_ts <= f.ts <= v.departure_ts and f.frame_idx in raw
        }

    raw_boxes = track_boxes(primary, data, fps)
    boxes = dict(raw_boxes)

    # Вид главного ТС собирается из тех же самых per-visit боксов, а не
    # считается заново: иначе разметка в веб-UI и счёт разошлись бы по
    # стабилизации — ровно та ошибка, ради которой заводился build_scene.
    for v in visits:
        if v.vehicle_track_id == primary.track_id:
            boxes.update(per_visit[v.visit_id])

    return Scene(
        primary=primary, visits=visits, boxes=boxes, raw_boxes=raw_boxes,
        visit_boxes=per_visit, raw_visit_boxes=raw_per_visit,
    )
