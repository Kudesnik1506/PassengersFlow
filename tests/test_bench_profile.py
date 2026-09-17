"""Разбор профиля на участки: вырожденный профиль не должен становиться одним куском.

Классические методы сводят борт к профилю «насколько эта колонка похожа на
дверь» и режут его порогом-процентилем. У ровного борта профиль почти везде
нулевой — и процентиль тоже оказывается нулём. Нестрогое сравнение «не ниже
порога» в этот момент объявляет выше порога ВЕСЬ кадр: вместо шести границ
створок получается один сплошной участок во всю ширину, из которого ни одной
двери не выделить.

Найдено прогоном обвязки на синтетическом кадре с тремя явными проёмами:
метод краёв нашёл ноль дверей из трёх.
"""

from __future__ import annotations

import numpy as np
from paxcount.bench.methods.classic import _runs


def test_flat_profile_with_spikes_yields_the_spikes_not_the_whole_width():
    profile = np.zeros(512, dtype=float)
    for x in (42, 77, 213, 247, 384, 418):
        profile[x] = 1.0
    runs = _runs(profile, percentile=88)
    assert len(runs) == 6, "шесть границ створок, а не один участок во всю ширину"
    assert all(end - start <= 2 for start, end, _ in runs)


def test_ordinary_profile_still_splits_into_peaks():
    rng = np.random.default_rng(0)
    profile = rng.random(512) * 0.2
    profile[100:120] += 1.0
    profile[300:320] += 1.0
    runs = _runs(profile, percentile=92)
    assert 2 <= len(runs) <= 4


def test_constant_profile_has_no_runs_at_all():
    """Ровный профиль без единого всплеска — двери не видно, и это честный ноль."""
    assert _runs(np.full(512, 0.7), percentile=88) == []
