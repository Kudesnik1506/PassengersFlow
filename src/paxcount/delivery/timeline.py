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
from dataclasses import dataclass, field
from datetime import date as Date
from datetime import datetime, timedelta

# Длительность куска, когда измерить её не по чему: смена из одного файла.
# Не «правило», а последнее средство — обычно длительность берётся из соседа
# (см. `Session._measure`), чтобы смена нарезки оператором не осталась
# незамеченной.
DEFAULT_SLOT_SECONDS = 600.0

# Разрыв, начиная с которого куски считаются разными сменами. Съёмка идёт тремя
# окнами (утро, день, вечер) с перерывами в часы, а куски внутри окна встык.
# Порог в полторы длительности куска разделяет эти два случая с запасом и не
# требует знать расписание.
SESSION_GAP_FACTOR = 1.5

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
    # Заполняется при сборке смены, когда виден сосед справа. Одиночный слот
    # соседа не имеет и остаётся с длительностью по умолчанию.
    duration_s: float = field(default=DEFAULT_SLOT_SECONDS, compare=False)

    @property
    def date(self) -> Date:
        return self.start.date()

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
    return FileSlot(
        start=start, index=int(match["index"]), stop=match["stop"].strip(), name=stem.strip()
    )


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
    def duration_s(self) -> float:
        return sum(s.duration_s for s in self.slots)

    def locate(self, offset_s: float) -> tuple[FileSlot, float]:
        """Переводит сквозное смещение смены в пару «кусок, смещение в нём».

        Нужно там, где надо открыть конкретный файл (нарезка кадров) или
        записать его имя в колонку N.
        """
        if offset_s < 0 or offset_s > self.duration_s:
            raise ValueError(
                f"смещение {offset_s:.1f} с вне смены длиной {self.duration_s:.1f} с"
            )
        left = offset_s
        for slot in self.slots:
            if left < slot.duration_s or slot is self.slots[-1]:
                return slot, left
            left -= slot.duration_s
        raise AssertionError("недостижимо: последний кусок возвращается выше")

    def at(self, offset_s: float) -> datetime:
        return self.start + timedelta(seconds=offset_s)

    def hours_minutes(self, offset_s: float) -> tuple[int, int]:
        moment = self.at(offset_s)
        return moment.hour, moment.minute

    def at_edge(self, offset_s: float, guard_s: float) -> bool:
        """Не у самого ли края смены момент — то есть не тот ли это случай N/A.

        Инструкция: если в начале съёмки транспорт уже стоит с открытыми
        дверями, в «Зашло» и «Вышло» ставится N/A; то же в конце. Речь о начале
        и конце СЪЁМКИ, а не каждого куска: внутри смены запись непрерывна, и
        поводов для N/A там нет.
        """
        return offset_s <= guard_s or offset_s >= self.duration_s - guard_s


def sessions_from_names(
    names: list[str], default_slot_s: float = DEFAULT_SLOT_SECONDS
) -> list[Session]:
    """Собирает смены из имён файлов: сортирует, меряет куски, режет по разрывам."""
    slots = sorted({parse_slot(n) for n in names})
    if not slots:
        return []

    groups: list[list[FileSlot]] = [[slots[0]]]
    for prev, cur in zip(slots, slots[1:]):
        gap = (cur.start - prev.start).total_seconds()
        expected = _measure(prev, cur, default_slot_s)
        if gap > expected * SESSION_GAP_FACTOR:
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
            measured.append(
                FileSlot(
                    start=slot.start, index=slot.index, stop=slot.stop,
                    name=slot.name, duration_s=float(seconds),
                )
            )
        sessions.append(Session(slots=tuple(measured)))
    return sessions


def _measure(prev: FileSlot, cur: FileSlot, default_s: float) -> float:
    """Ожидаемая длительность куска: по факту, если он измерим, иначе объявленная."""
    gap = (cur.start - prev.start).total_seconds()
    return gap if 0 < gap <= default_s * SESSION_GAP_FACTOR else default_s
