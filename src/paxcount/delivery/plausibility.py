"""Сторож правдоподобия: похоже ли это на смену на живой остановке.

Допуск ЦТП нам неизвестен и известен не будет — заказчик это подтвердил. Значит
вопрос «попали ли мы в их допуск» задать нечему. Но есть вопрос, который задать
можно: «бывает ли такое вообще». На него отвечает принятая работа — файл, за
который заказчику уже заплатили: 312 ТС, 1334 вышедших, 830 вошедших.

Это не правила и ничего не блокирует. Это разница между «наш прогон ошибся на
человека» (не увидим и не должны) и «наш прогон выдал вдвое больше людей, чем
бывает» (увидим до того, как отправим).

Ориентир — одна смена одной остановки, и переносить его на другие остановки
буквально нельзя. Поэтому здесь не пороги приёмки, а кратность отклонения:
вопрос ставится как «во сколько раз не похоже», а не «выше или ниже числа».
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass

from .model import DeliveryRow


@dataclass(frozen=True)
class Reference:
    """Ориентир из принятой заказчиком таблицы 2026-05-19, ОП 1715-15182.

    Замерено по 310 строкам с проставленным счётом (ещё две — N/A).
    """

    mean_alighted: float
    mean_boarded: float
    max_alighted: int
    max_boarded: int
    zero_alighted_share: float
    zero_boarded_share: float
    vehicles_per_hour_peak: int


REFERENCE = Reference(
    mean_alighted=1334 / 310,
    mean_boarded=830 / 310,
    max_alighted=30,
    max_boarded=14,
    zero_alighted_share=26 / 310,
    zero_boarded_share=53 / 310,
    vehicles_per_hour_peak=40,
)

# Во сколько раз среднее должно разойтись с ориентиром, чтобы это стоило
# внимания. Полуторакратное расхождение — уже не разница остановок, а разница
# методов счёта.
MEAN_FACTOR = 1.5
# Ниже какой доли нулей смена перестаёт быть похожей на живую. У заказчика
# 8 % пустых по выходу и 17 % по входу; полное отсутствие нулей означает
# счётчик, который всегда что-нибудь находит.
MIN_ZERO_SHARE = 0.02


@dataclass(frozen=True)
class Suspicion:
    """Не нарушение, а повод посмотреть глазами до отправки."""

    field: str
    message: str


def _mean(values: list[int]) -> float:
    return sum(values) / len(values) if values else 0.0


def suspicions(rows: list[DeliveryRow]) -> list[Suspicion]:
    """Чем прогон не похож на принятую работу. Пустой список — похож."""
    counted = [r for r in rows if r.counted]
    if not counted:
        return []

    out: list[Suspicion] = []
    alighted = [r.alighted for r in counted]
    boarded = [r.boarded for r in counted]

    for name, values, ref_mean, ref_max in (
        ("вышло", alighted, REFERENCE.mean_alighted, REFERENCE.max_alighted),
        ("зашло", boarded, REFERENCE.mean_boarded, REFERENCE.max_boarded),
    ):
        mean = _mean(values)
        if mean > ref_mean * MEAN_FACTOR or mean * MEAN_FACTOR < ref_mean:
            out.append(Suspicion(
                name,
                f"среднее «{name}» {mean:.1f} против ориентира {ref_mean:.1f} "
                f"по принятой работе — расхождение в {mean / ref_mean:.1f} раза",
            ))
        if values and max(values) > ref_max:
            out.append(Suspicion(
                name,
                f"максимум «{name}» {max(values)} выше наблюдавшегося {ref_max} — "
                "проверить эту строку глазами",
            ))

    for name, values, ref_share in (
        ("вышло", alighted, REFERENCE.zero_alighted_share),
        ("зашло", boarded, REFERENCE.zero_boarded_share),
    ):
        share = sum(1 for v in values if v == 0) / len(values)
        if share < MIN_ZERO_SHARE:
            out.append(Suspicion(
                name,
                f"нулей по «{name}» {share:.0%} против {ref_share:.0%} в принятой "
                "работе — счётчик, который всегда что-то находит, выдумывает",
            ))

    per_hour = Counter(r.hours for r in rows)
    if per_hour:
        peak_hour, peak = per_hour.most_common(1)[0]
        if peak > REFERENCE.vehicles_per_hour_peak * MEAN_FACTOR:
            out.append(Suspicion(
                "поток",
                f"в {peak_hour}:00 насчитано {peak} ТС при наблюдавшемся пике "
                f"{REFERENCE.vehicles_per_hour_peak} — похоже на двойной счёт визитов",
            ))
    return out
