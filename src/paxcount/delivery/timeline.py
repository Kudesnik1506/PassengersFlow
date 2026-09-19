"""Шкала времени смены, собранная из имён файлов.

Оператор отдаёт смену нарезкой по десять минут: `2026-05-19 - 06-59-54 -
1715-15182 - 01`, следом ровно `07-09-54`. Куски идут встык, то есть это одна
непрерывная запись, а не 54 отдельных ролика.

Отсюда устройство модуля: смена собирается в **сплошную шкалу**, и визит,
попавший на разрез файла, перестаёт быть особым случаем — он просто лежит на
этой шкале. Иначе 54 разреза за день породили бы 54 частных случая, каждый со
своей эвристикой сшивки.

Время камеры при этом берётся из имени файла, а не распознаётся в кадре.
Инструкция расшифровщика называет время камеры основным источником, а выгрузку
оператора запасным («если оно неверное или не отображается»), — имя файла даёт
то же самое даром и без ошибок распознавания.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta


class NoFootageError(ValueError):
    """Смещение попадает в разрыв записи — там нет ни файла, ни кадра.

    Наследует ValueError: код, который уже ловит ValueError от `locate()`
    (диапазон смены), продолжает работать и здесь без правки.
    """

# Длительность куска, когда измерить её не по чему: смена из одного файла.
# Не «правило», а последнее средство — обычно длительность берётся из соседа
# (см. `Session._measure`), чтобы смена нарезки оператором не осталась
# незамеченной.
DEFAULT_SLOT_SECONDS = 600.0

# Разрыв, начиная с которого куски считаются разными сменами. Порог абсолютный,
# и это следствие замера, а не удобства: в 125 боевых файлах трёх камер самый
# длинный кусок — 31 минута (регистратор длиннее не пишет), а самый короткий
# перерыв между сменами — 119 минут. Час лежит между ними с двукратным запасом
# в обе стороны.
#
# Относительный порог здесь не работает ни в какую сторону. Камеры пишут
# по-разному: первая ровно по 25 минут, третья по 10, вторая кусками от 23 с
# до 31 минуты. Полторы медианы разрезали бы смену второй камеры посередине,
# а пять медиан не разделили бы смены первой.
SESSION_GAP_S = 3600.0

_NAME_RE = re.compile(
    r"^(?P<date>\d{4}-\d{2}-\d{2})"
    r"\s*-\s*(?P<time>\d{2}-\d{2}-\d{2})"
    r"\s*-\s*(?P<stop>.+?)"
    r"\s*-\s*(?P<index>\d+)$"
)


@dataclass(frozen=True, order=True)
class FileSlot:
    """Один кусок записи: когда начался, какой остановке принадлежит.

    `order=True` и `frozen=True` не для красоты: слоты складываются в множества
    при отсеивании дублей и сортируются при сборке смены. Первым полем идёт
    `start` — сортировка по времени и есть нужный порядок.
    """

    start: datetime
    index: int
    stop: str
    name: str
    # Номер камеры из имени файла: `22739_2` — остановка 22739, камера 2.
    # Пустая строка у съёмки без камер в имени (так названа принятая смена
    # заказчика). Стоит перед `duration_s`, чтобы позиционные вызовы из
    # четырёх аргументов продолжали значить то же, что и раньше.
    camera: str = ""
    # Заполняется при сборке смены, когда виден сосед справа. Одиночный слот
    # соседа не имеет и остаётся с длительностью по умолчанию.
    #
    # Это расстояние ДО СТАРТА СЛЕДУЮЩЕГО ФАЙЛА, а не длительность самой
    # записи — при разрыве в записи (боевая К2) они расходятся, и `duration_s`
    # молча накрывает собой и файл, и дыру после него. Именно поэтому
    # `at`/`locate` продолжают работать с ним как со сквозной шкалой времени, а
    # настоящая длительность записи — отдельное поле ниже.
    duration_s: float = field(default=DEFAULT_SLOT_SECONDS, compare=False)
    # Настоящая длительность ЗАПИСИ (ffprobe), а не расстояние до соседа.
    # `None` — не измерена: тогда файл считается идущим без разрыва до конца
    # `duration_s`, как и раньше `sessions_from_names` без `duration_of`.
    real_duration_s: float | None = field(default=None, compare=False)

    @property
    def date(self) -> Date:
        return self.start.date()

    @property
    def gap_after_s(self) -> float:
        """Разрыв записи между концом этого файла и стартом следующего.

        0 — либо разрыва нет, либо настоящая длительность не измерена (тогда
        файл считается непрерывным до соседа, как раньше).
        """
        if self.real_duration_s is None:
            return 0.0
        return max(0.0, self.duration_s - self.real_duration_s)

    def at(self, offset_s: float) -> datetime:
        """Абсолютное время момента, отстоящего на `offset_s` от начала куска."""
        return self.start + timedelta(seconds=offset_s)

    def hours_minutes(self, offset_s: float) -> tuple[int, int]:
        """Часы и минуты прибытия — колонки C и D таблицы заказчика.

        Секунды отбрасываются, а не округляются: округление вверх сдвинуло бы
        часть прибытий в следующую минуту, и на плотной остановке (46 % машин
        делят минуту с соседней) это меняло бы сам порядок строк.
        """
        moment = self.at(offset_s)
        return moment.hour, moment.minute


def parse_slot(name: str) -> FileSlot:
    """Разбирает имя файла оператора. Чужое имя — ошибка, а не пустое значение.

    Молча вернуть «кусок с нулевым временем» здесь нельзя: время прибытия
    попадёт в отчёт заказчику, и ошибка соглашения об именах обязана
    остановить работу, а не разъехаться по 312 строкам.
    """
    stem = name.strip()
    for suffix in (".mp4", ".webm", ".ogv", ".mov", ".avi", ".mkv", ".m4v"):
        if stem.lower().endswith(suffix):
            stem = stem[: -len(suffix)]
            break
    match = _NAME_RE.match(stem.strip())
    if not match:
        raise ValueError(
            f"имя файла не по соглашению оператора: {name!r}. "
            "Ожидается «ГГГГ-ММ-ДД - ЧЧ-ММ-СС - остановка - номер»"
        )
    try:
        start = datetime.strptime(
            f"{match['date']} {match['time']}", "%Y-%m-%d %H-%M-%S"
        )
    except ValueError as exc:
        raise ValueError(f"дата или время в имени файла не существуют: {name!r}") from exc
    stop, camera = _split_camera(match["stop"].strip())
    return FileSlot(
        start=start, index=int(match["index"]), stop=stop, name=stem.strip(), camera=camera
    )


def _split_camera(field_value: str) -> tuple[str, str]:
    """Отделяет номер камеры от номера остановки: `22739_2` → `22739`, `2`.

    Разделитель — подчёркивание, и это не догадка: так названы все 103 файла
    боевой съёмки. Принятая смена заказчика (`1715-15182`) подчёркивания не
    содержит вовсе и остаётся остановкой без камеры — старые имена читаются
    по-прежнему.
    """
    stop, sep, tail = field_value.rpartition("_")
    if sep and tail.isdigit():
        return stop, tail
    return field_value, ""


@dataclass(frozen=True)
class Session:
    """Смена: непрерывная лента, собранная из идущих встык кусков.

    Смещение внутри смены (`offset_s`) — сквозное, от начала первого куска.
    Именно оно, а не «секунда внутри файла», служит шкалой для визитов: в этой
    системе координат разрез между файлами не существует.
    """

    slots: tuple[FileSlot, ...]

    @property
    def start(self) -> datetime:
        return self.slots[0].start

    @property
    def date(self) -> Date:
        return self.slots[0].date

    @property
    def stop(self) -> str:
        return self.slots[0].stop

    @property
    def camera(self) -> str:
        """Смена принадлежит одной камере: три камеры — три ленты, не одна."""
        return self.slots[0].camera

    @property
    def duration_s(self) -> float:
        return sum(s.duration_s for s in self.slots)

    def locate(self, offset_s: float) -> tuple[FileSlot, float]:
        """Переводит сквозное смещение смены в пару «кусок, смещение в нём».

        Нужно там, где надо открыть конкретный файл (нарезка кадров) или
        записать его имя в колонку N. Смещение внутри разрыва записи (боевая
        К2 теряет так 1074 с в главный провал) — не «дальше в этом файле», а
        отсутствие записи: там нет ни файла, ни кадра, который можно открыть.
        """
        if offset_s < 0 or offset_s > self.duration_s:
            raise ValueError(
                f"смещение {offset_s:.1f} с вне смены длиной {self.duration_s:.1f} с"
            )
        left = offset_s
        for slot in self.slots:
            is_last = slot is self.slots[-1]
            if left < slot.duration_s or is_last:
                # У последнего куска duration_s — не измеренное расстояние до
                # соседа, а оценка (см. sessions_from_names): настоящего
                # соседа нет, и утверждать про дыру после него нечем.
                if not is_last and slot.real_duration_s is not None and left > slot.real_duration_s:
                    raise NoFootageError(
                        f"смещение {offset_s:.1f} с попадает в разрыв записи после "
                        f"{slot.name!r} ({slot.real_duration_s:.1f} с записи, "
                        f"{slot.gap_after_s:.1f} с дыры)"
                    )
                return slot, left
            left -= slot.duration_s
        raise AssertionError("недостижимо: последний кусок возвращается выше")

    def gaps_s(self) -> list[tuple[float, float]]:
        """Разрывы записи на сквозной шкале смены: (где начинается, длина).

        Пусто, если настоящая длительность файлов не измерена (`duration_of`
        не передавался в `sessions_from_names`) — тогда смена считается
        непрерывной, как и раньше. Последний кусок не участвует: его
        `duration_s` — оценка, а не измеренное расстояние до соседа, и дыру
        после него утверждать нечем.
        """
        gaps: list[tuple[float, float]] = []
        cursor = 0.0
        for slot in self.slots[:-1]:
            if slot.gap_after_s > 0:
                gaps.append((cursor + slot.real_duration_s, slot.gap_after_s))
            cursor += slot.duration_s
        return gaps

    def at(self, offset_s: float) -> datetime:
        return self.start + timedelta(seconds=offset_s)

    def hours_minutes(self, offset_s: float) -> tuple[int, int]:
        moment = self.at(offset_s)
        return moment.hour, moment.minute

    def at_edge(self, offset_s: float, guard_s: float) -> bool:
        """Не у самого ли края смены или разрыва момент — случай N/A.

        Инструкция: если в начале съёмки транспорт уже стоит с открытыми
        дверями, в «Зашло» и «Вышло» ставится N/A; то же в конце. Раньше речь
        шла только о начале и конце СЪЁМКИ — но разрыв записи внутри смены
        (боевая К2, 1074 с в разгар пика) устроен так же: визит, начавшийся
        прямо перед дырой или сразу после неё, виден лишь частично, и это тот
        же случай N/A, а не полноценный счёт (план, шаг 0б).
        """
        if offset_s <= guard_s or offset_s >= self.duration_s - guard_s:
            return True
        for gap_start, gap_len in self.gaps_s():
            if gap_start - guard_s <= offset_s <= gap_start + gap_len + guard_s:
                return True
        return False


def sessions_from_names(
    names: list[str],
    default_slot_s: float = DEFAULT_SLOT_SECONDS,
    duration_of: Callable[[str], float] | None = None,
) -> list[Session]:
    """Собирает смены из имён файлов: сортирует, меряет куски, режет по разрывам.

    `duration_of` — внешняя мера настоящей длительности записи по имени файла
    (в бою — ffprobe, см. `tools/coverage.py`). Без неё поведение не меняется:
    длительность куска по-прежнему берётся по соседу, а разрывов внутри смены
    не видно — так работал модуль до появления карты покрытия.
    """
    slots = sorted({parse_slot(n) for n in names})
    if not slots:
        return []

    # Сначала по камере, и только потом по разрывам. Камеры пишут с разным
    # шагом — 25 минут, 10 минут и куски переменной длины — и стартуют
    # вразнобой. В общем отсортированном списке соседом куска оказывался
    # кусок чужой камеры, и длительность мерялась по чужому шагу.
    groups: list[list[FileSlot]] = []
    for key in sorted({(s.stop, s.camera) for s in slots}):
        track = [s for s in slots if (s.stop, s.camera) == key]
        groups.append([track[0]])
        for prev, cur in zip(track, track[1:]):
            if (cur.start - prev.start).total_seconds() > SESSION_GAP_S:
                groups.append([cur])
            else:
                groups[-1].append(cur)

    sessions: list[Session] = []
    for group in groups:
        measured: list[FileSlot] = []
        for i, slot in enumerate(group):
            if i + 1 < len(group):
                seconds = (group[i + 1].start - slot.start).total_seconds()
            elif measured:
                # У последнего куска нет соседа справа — берём длительность
                # предыдущего: она уже измерена по этой же записи.
                seconds = measured[-1].duration_s
            else:
                seconds = default_slot_s
            real = duration_of(slot.name) if duration_of is not None else None
            measured.append(
                FileSlot(
                    start=slot.start, index=slot.index, stop=slot.stop,
                    name=slot.name, camera=slot.camera, duration_s=float(seconds),
                    real_duration_s=None if real is None else float(real),
                )
            )
        sessions.append(Session(slots=tuple(measured)))
    return sessions



def gaps_between(slots: list[FileSlot]) -> list[tuple[datetime, float]]:
    """Дыры записи по плоскому списку кусков: (когда началась, сколько секунд).

    `Session.gaps_s` отвечает про одну смену, а книга собирается на сутки
    (решение 072): у К2 за день три смены с двухчасовыми перерывами между
    ними, и строке, попавшей в перерыв, нужен тот же ответ, что и строке
    внутри разрыва смены, — записи нет.

    Кусок без измеренной длительности дыры за собой не заявляет: «не измерено»
    и «писала без перерыва» — разные вещи (`FileSlot.gap_after_s`).
    """
    out: list[tuple[datetime, float]] = []
    ordered = sorted(slots)
    for slot, following in zip(ordered, ordered[1:]):
        if slot.real_duration_s is None:
            continue
        ends = slot.at(slot.real_duration_s)
        seconds = (following.start - ends).total_seconds()
        if seconds > 0:
            out.append((ends, seconds))
    return out


@dataclass(frozen=True)
class CameraTrack:
    """Лента одной камеры: её куски записи и поправка к общей шкале.

    Поправка та же, что в `clocks.ClockRecord`: `raw − offset = reference`.
    Здесь она на КАМЕРУ, а не на файл, и это допущение: смена — сплошная лента
    (решение 027), её куски нарезает одна и та же камера одними и теми же
    часами. Файл с собственной поправкой в эту картину не укладывается, и
    такому случаю здесь места нет — он решается таблицей `clocks`.
    """

    camera: str
    offset_to_reference_s: float
    slots: list[FileSlot]


@dataclass(frozen=True)
class Footage:
    """Запись, в которой лежит момент: чья камера, какой файл, её время."""

    camera: str
    file: str
    camera_ts: datetime


def footage_at(moment: datetime, cameras: list[CameraTrack]) -> Footage | None:
    """Первая камера из списка, писавшая в этот момент общей шкалы.

    Порядок списка — политика вызывающего: этот модуль не знает, какая камера
    предпочтительнее, он знает только, какая писала. `None` — не писал никто,
    и графа остаётся пустой: назвать соседний файл значило бы отправить
    проверяющего смотреть не тот кусок записи.

    Момент переводится в часы КАЖДОЙ камеры отдельно. Без перевода на К3,
    отстающей на 418 с, был бы назван сосед через файл — ошибка тихая, потому
    что имя файла выглядит правдоподобно.
    """
    for track in cameras:
        raw = moment + timedelta(seconds=track.offset_to_reference_s)
        name = file_at(raw, track.slots)
        if name:
            return Footage(camera=track.camera, file=name, camera_ts=raw)
    return None


def file_at(moment: datetime, slots: list[FileSlot]) -> str:
    """Имя файла, внутри которого лежит момент. Пусто — записи на него нет.

    Графа N бланка называется «Название видеофайла. Скопировать сюда»: по ней
    проверяющий открывает запись. Имя файла, момента в котором нет, хуже
    пустой графы — оно отправляет смотреть не туда и выглядит заполненным.

    Поэтому границу задаёт ИЗМЕРЕННАЯ длительность записи, а не расстояние до
    соседнего файла: у боевой К2 между файлами есть дыры (главная — 1074 с), и
    момент, попавший в дыру, не снят вовсе. Длительность не измерена — файл
    считается идущим до соседа, как и везде в этом модуле.
    """
    started = [s for s in sorted(slots) if s.start <= moment]
    if not started:
        return ""
    slot = started[-1]
    length = slot.real_duration_s if slot.real_duration_s is not None else slot.duration_s
    return slot.name if (moment - slot.start).total_seconds() <= length else ""
