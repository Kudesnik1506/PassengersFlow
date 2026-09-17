"""Карта покрытия смены: где в записи разрывы и что за это время пропущено.

Разрыв — не абстрактная дыра, а конкретные машины: главный провал боевой К2
(07:26:42–07:44:36, 1074 с) приходится на разгар пика — 10 записей оператора
внутри него. Отчёт держит длину дыры и число записей оператора рядом, потому
что по одной длине нельзя судить о цене: 1074 с в пик и 1074 с в затишье —
разная потеря (план, шаг 0б — это первая проверка, ещё до счёта).

Логика отделена от `tools/coverage.py` намеренно: здесь только чистые функции
над уже собранной `Session` (тестируются без ffprobe и без боевых видео),
доступ к ffprobe и к выгрузке оператора — дело тонкого скрипта в `tools/`.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timedelta

from .model import DeliveryRow
from .reconcile import notes_for_visit
from .timeline import CameraTrack, Session, footage_at, gaps_between


# Ниже этой длины пропуск между файлами — технический стык, а не разрыв, о
# котором нужно знать: 2-4 с между кусками у боевых камер обычное дело. Порог
# живёт здесь, а не в скрипте, потому что им пользуются оба потребителя — и
# отчёт о покрытии, и графа комментария в книге (принцип 2).
MIN_GAP_S = 5.0


@dataclass(frozen=True)
class Gap:
    """Один разрыв записи на шкале конкретной камеры."""

    camera: str
    start: datetime
    duration_s: float
    operator_records_inside: int


def gaps_for(
    camera: str,
    session: Session,
    min_gap_s: float,
    operator_count: Callable[[datetime, datetime], int],
) -> list[Gap]:
    """Разрывы записи не короче `min_gap_s`, каждый — с числом записей оператора.

    Мелкие стыки между файлами (2-4 с — обычное дело у боевых камер) не разрыв,
    о котором нужно знать: `min_gap_s` их отсекает, чтобы значимая дыра не
    терялась в техническом шуме. Без измеренной настоящей длительности файлов
    (`Session.gaps_s()` пуст) разрывов не видно — это не ошибка, а честное
    отсутствие данных, см. `tools/coverage.py`.
    """
    out: list[Gap] = []
    for offset, length in session.gaps_s():
        if length < min_gap_s:
            continue
        start = session.at(offset)
        end = session.at(offset + length)
        out.append(Gap(
            camera=camera, start=start, duration_s=length,
            operator_records_inside=operator_count(start, end),
        ))
    return out


def render_report(gaps: list[Gap]) -> str:
    """Отчёт для человека: одна строка на разрыв, отсортировано по времени."""
    if not gaps:
        return "разрывов записи нет"
    lines = [
        f"камера {g.camera}: {g.start:%H:%M:%S} + {g.duration_s:.0f} с "
        f"({g.operator_records_inside} записей оператора внутри)"
        for g in sorted(gaps, key=lambda g: g.start)
    ]
    return "\n".join(lines)


def with_footage(row: DeliveryRow, moment: datetime,
                  cameras: list[CameraTrack], gaps: list[Gap]) -> DeliveryRow:
    """Строка с записью: имя файла, камера с её часами и причина, если записи нет.

    Файл ищется по ЛЮБОЙ камере, писавшей в этот момент, а не только по опорной:
    К2 теряет 1074 с в главный провал, К1 в это же время пишет без единого
    перерыва, и пустая графа читалась бы как «не снято» там, где снято.

    Разрыв называется в комментарии ДАЖЕ ЕСЛИ запись нашлась у другой камеры.
    Он не перестал быть аномалией оттого, что его закрыли: именно он объясняет
    проверяющему, почему у соседних строк файлы разных камер, — а заказчик
    просил аномалию съёмки отражать словами (`reconcile.gap_note`).
    """
    notes = notes_for_visit(moment, gaps)
    found = footage_at(moment, cameras)
    if found is None:
        return row.model_copy(update={"video": "", "camera": "",
                                       "camera_ts": None, "notes": notes})
    return row.model_copy(update={"video": found.file, "camera": found.camera,
                                   "camera_ts": found.camera_ts, "notes": notes})


def gaps_of_cameras(cameras: list[CameraTrack],
                     min_gap_s: float = MIN_GAP_S) -> list[Gap]:
    """Разрывы всех камер, сведённые на общую шкалу и очищенные от стыков.

    На общую шкалу — потому что с ней сверяет проверяющий: время строки и время
    разрыва обязаны идти в одних часах, иначе «попадает или нет» не прочесть
    (`reconcile.gap_note`). Число записей оператора внутри здесь не считается:
    это забота отчёта о покрытии, а в книге разрыв и так стоит рядом со
    строками, которые в него попали.
    """
    out: list[Gap] = []
    for track in cameras:
        for start, seconds in gaps_between(track.slots):
            if seconds < min_gap_s:
                continue
            out.append(Gap(
                camera=track.camera,
                start=start - timedelta(seconds=track.offset_to_k2_s),
                duration_s=seconds, operator_records_inside=0,
            ))
    return out
