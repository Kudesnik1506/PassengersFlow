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
from datetime import datetime

from .timeline import Session


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
