"""Схемы данных, общие для всех бэкендов подсчёта."""

from __future__ import annotations

from enum import Enum
from pathlib import Path

from pydantic import BaseModel, Field


class Direction(str, Enum):
    IN = "in"
    OUT = "out"


class Point(BaseModel):
    x: float
    y: float


class DoorSpec(BaseModel):
    """Геометрия одной двери.

    Координаты могут быть заданы двумя способами:
    - ``absolute``: пиксели кадра. Годится только для строго статичной камеры.
    - ``vehicle``: доля от bbox транспортного средства (0..1 по каждой оси).
      Устойчиво к движению камеры и к смене ракурса — см. решение №1 в плане.
    """

    door_id: str = "door"
    # line — считаем пересечения линии; zone — появление и исчезновение трека
    # внутри дверной зоны. Для наружной камеры работает именно zone: вошедший
    # человек не пересекает линию, он физически пропадает за корпусом.
    mode: str = Field(default="zone", pattern="^(line|zone)$")
    frame: str = Field(default="vehicle", pattern="^(absolute|vehicle)$")
    line_start: Point
    line_end: Point
    # Куда смотрит салон относительно линии. Знак векторного произведения
    # (end-start) x (point-start): положительный — одна сторона, отрицательный — другая.
    inside_sign: int = Field(default=-1, description="Знак стороны, считающейся салоном")
    # Ширина полосы у линии, в долях высоты bbox ТС. Пересечения дальше этой
    # полосы игнорируются — так отсеиваются прохожие на заднем плане.
    band: float = 0.25
    # Зона двери в долях bbox ТС: (x0, y0, x1, y1). По умолчанию — полоса ног
    # на всю ширину корпуса.
    #
    # Границы по вертикали не выбраны, а измерены: 7116 наблюдений по набору,
    # десятый процентиль точки опоры — 0.66 высоты кузова, медиана — 1.12.
    # Отсюда 0.65 и 1.12. Считается положение НОГ (`_anchor` берёт низ рамки
    # человека), а не высота дверного проёма: зона по проёму ног не накрывает,
    # а зона, поднятая выше 0.65, начинает ловить пассажиров, стоящих в салоне
    # и видимых сквозь дверь. Оба края этой ошибки уже стоили нам по полдня —
    # см. README, «Полоса ног, а не дверной проём».
    #
    # Прежнее значение (верх 0.33) не проходило собственный гейт разметки с
    # порогом 0.60. На размеченных роликах это не проявлялось — там зоны
    # заданы руками, — но фолбэк `doors.fallback_doors` строит дверь именно
    # со значением по умолчанию, и первая же запись без разметки получила бы
    # зону, захватывающую салон.
    zone: tuple[float, float, float, float] = (0.0, 0.65, 1.0, 1.12)

    def resolve_zone(
        self, bbox: tuple[float, float, float, float]
    ) -> tuple[float, float, float, float]:
        """Переводит зону в пиксели кадра с учётом bbox ТС."""
        if self.frame == "absolute":
            return self.zone
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        zx0, zy0, zx1, zy1 = self.zone
        return (x1 + zx0 * w, y1 + zy0 * h, x1 + zx1 * w, y1 + zy1 * h)

    def resolve(self, bbox: tuple[float, float, float, float]) -> tuple[Point, Point]:
        """Переводит линию в пиксели кадра с учётом bbox ТС."""
        if self.frame == "absolute":
            return self.line_start, self.line_end
        x1, y1, x2, y2 = bbox
        w, h = x2 - x1, y2 - y1
        return (
            Point(x=x1 + self.line_start.x * w, y=y1 + self.line_start.y * h),
            Point(x=x1 + self.line_end.x * w, y=y1 + self.line_end.y * h),
        )


class DoorSet(BaseModel):
    """Набор дверей для одного типа ТС.

    На остановке за смену останавливаются разные машины: маршрутка с одной
    дверью, 12-метровый автобус с тремя, сочленённый с четырьмя. Доли от рамки
    ТС переносятся между заездами ОДНОГО типа, но не между типами: там, где у
    автобуса средняя дверь, у маршрутки борт.

    Тип опознаётся по вытянутости рамки (ширина/высота) — величине, не
    зависящей от масштаба и расстояния до камеры, и доступной без второй
    модели. Замер по набору: поезд метро в кадре целиком ~1.4, маршрутка ~2.0,
    12-метровый автобус ~3.0, сочленённый ~4.5. Ограничение честное: автобус и
    троллейбус одной длины по этому признаку неразличимы — но у них и схема
    дверей одинаковая, так что для счёта это не мешает.
    """

    name: str = "default"
    aspect_min: float = 0.0
    aspect_max: float = 1e9
    doors: list[DoorSpec] = Field(default_factory=list)


class VideoConfig(BaseModel):
    """Конфиг разметки для одного видео. Лежит в data/zones/<stem>.json."""

    video: str
    doors: list[DoorSpec] = Field(default_factory=list)
    # Наборы дверей под разные типы ТС. Пустой список — обычный случай: на
    # ролике один тип, работает `doors`. Выбор набора — `doors_for`.
    door_sets: list[DoorSet] = Field(default_factory=list)
    # Класс ТС, за которым следим. По умолчанию — все крупные ТС COCO.
    vehicle_classes: list[str] = Field(default_factory=lambda: ["bus", "truck", "train"])
    # Ниже этой скорости (пикселей на кадр, доля от диагонали bbox) ТС считается стоящим.
    stationary_speed: float = 0.010
    # Порог устойчивости размера bbox (p90/p10 диагонали) внутри визита. Метрика смещения
    # центра слепа к движению ТС прямо на камеру (диагональ растёт, центр стоит на месте) —
    # см. решение в плане стабилизации bbox. Выше порога визит не считается стоянкой.
    stationary_scale: float = 1.25
    # Длина окна для канонического bbox внутри стационарного визита, секунды.
    # 5с — компромисс, подобранный на видео с реальным покачиванием кузова
    # (артикулированный автобус на подвеске). Там, где такого покачивания
    # нет — окно можно расширить: остаточный разброс между окнами там чистый
    # шум детектора, и более длинное окно его усредняет лучше.
    canonical_window_seconds: float = 5.0
    # Минимальная длительность стоянки, секунды.
    min_visit_seconds: float = 1.5
    # Ниже этой доли площади кадра трек ТС не рассматривается как кандидат на
    # визит. Визиты ищутся по всем ТС сразу (см. build_scene), поэтому нужен
    # отсев фона: автобус на соседней улице тоже стоит, то есть формально даёт
    # визит, и дверные зоны, применённые к нему, ловили бы прохожих на заднем
    # плане. Порог физический, а не подогнанный: 5% кадра 1280x720 — это ТС
    # примерно 230x110 px, при котором одна дверь занимает ~30 px по ширине,
    # то есть столько же, сколько бокс человека рядом с ней; приписать событие
    # конкретной двери на таком масштабе уже нельзя. Замер по набору из 15
    # роликов: целевые ТС занимают 10-75% кадра, фоновые — 0.2-4%.
    min_vehicle_area: float = 0.05
    # Гистерезис: сколько кадров новая сторона должна продержаться.
    hysteresis_frames: int = 3
    # True, если конфиг пришёл из ручной разметки: тогда автоуточнение зон
    # не трогает то, что нарисовал человек.
    manual: bool = False
    notes: str = ""
    # bbox ТС (в пикселях кадра reference_frame), относительно которого человек рисовал
    # зоны в веб-UI. Позволяет счёту заметить рассинхрон систем координат и позволяет
    # `paxcount doors remap` пересчитать доли при смене конвенции канонического bbox.
    reference_box: tuple[float, float, float, float] | None = None
    reference_frame: int | None = None

    @classmethod
    def default_for(cls, video: Path) -> "VideoConfig":
        return cls(video=video.name, doors=[])

    def doors_for(self, aspect: float | None) -> list[DoorSpec]:
        """Двери под конкретное ТС по вытянутости его рамки.

        Без `door_sets` (обычный случай) всегда возвращает `doors` — поведение
        роликов с одним типом ТС не меняется. Если подходящего набора нет,
        тоже отдаёт `doors`: молча не считать по чужой геометрии хуже, чем
        посчитать по общей.
        """
        if not self.door_sets or aspect is None:
            return self.doors
        for ds in self.door_sets:
            if ds.aspect_min <= aspect <= ds.aspect_max:
                return ds.doors
        return self.doors


class PersonEvent(BaseModel):
    """Одно пересечение: человек вошёл или вышел."""

    video: str
    visit_id: int
    event_ts: float
    frame_idx: int
    person_track_id: int
    door_id: str
    direction: Direction
    confidence: float = 1.0


class VehicleVisit(BaseModel):
    """Один визит ТС на остановку — основная строка отчёта."""

    video: str
    visit_id: int
    vehicle_track_id: int
    arrival_ts: float
    departure_ts: float
    boarded: int = 0
    alighted: int = 0
    backend: str = ""
    confidence: float = 1.0
    # Настоящая стоянка (bbox устойчив по размеру и центру), а не эрзац-визит на всё видео,
    # когда детект визитов не нашёл ни одного, и не движение прямо на/от камеры. Только
    # такие визиты стабилизируются каноническим bbox — см. build_scene в visits.py.
    stationary: bool = True


class RunResult(BaseModel):
    video: str
    backend: str
    duration_s: float
    fps: float
    frames_processed: int
    wall_seconds: float
    visits: list[VehicleVisit] = Field(default_factory=list)
    events: list[PersonEvent] = Field(default_factory=list)

    @property
    def boarded(self) -> int:
        return sum(v.boarded for v in self.visits)

    @property
    def alighted(self) -> int:
        return sum(v.alighted for v in self.visits)
