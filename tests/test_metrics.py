"""Метрики качества счёта — по образцу отраслевой практики (VDV 457).

Почему не хватает одной суммарной погрешности:

1. Она не различает «ошиблись в обе стороны» и «систематически недосчитываем».
   Для планирования перевозок это принципиально разные вещи: разнонаправленные
   ошибки на сотне остановок гасят друг друга, систематические накапливаются.
   Поэтому в VDV 457 главный критерий — смещение (bias), а не модуль ошибки.

2. Она неисчислима там, где эталон нулевой. У нас таких роликов четыре из шести
   размеченных, и это самые ценные случаи: они проверяют, что система не
   срабатывает на пустом месте. Отраслевой ответ — допуск в пассажирах, а не в
   процентах: доля остановок, где ошиблись не больше чем на одного и на двух.
   В стандарте этот случай оговорён явно — «включая остановки, где посадки и
   высадки не было».

Единица измерения здесь — пара «видео + направление», а не остановка: эталон
размечен по видео. Когда эталон станет повизитным, единицей станет визит, а сами
метрики не изменятся.
"""

from __future__ import annotations

import math

from paxcount.evaluate import (
    KNOWN_DOMAINS,
    TARGET_DOMAIN,
    count_quality,
    quality_by_domain,
    unknown_domains,
)


def test_exact_counts_give_full_shares():
    q = count_quality([(3, 3), (0, 0), (7, 7)])
    assert q.units == 3
    assert q.exact == 1.0
    assert q.within_1 == 1.0
    assert q.within_2 == 1.0
    assert q.bias == 0.0
    assert q.error == 0.0


def test_zero_truth_units_are_counted_not_skipped():
    """Ролики с нулевым эталоном обязаны попадать в доли.

    Именно они проверяют, что система не выдумывает событий на пустом месте.
    Процентная метрика их теряет — деление на ноль, — и потому самая ценная
    часть набора оказывалась вне оценки.
    """
    q = count_quality([(0, 0), (0, 0), (1, 0)])
    assert q.units == 3
    assert q.exact == 2 / 3
    assert q.within_1 == 1.0


def test_bias_is_signed_and_error_is_not():
    """Ключевое различие: недосчёт и перебор — разные дефекты."""
    under = count_quality([(7, 12)])
    over = count_quality([(17, 12)])

    assert under.bias < 0
    assert over.bias > 0
    assert under.error == over.error  # модуль ошибки одинаков


def test_compensating_errors_hide_in_error_but_show_in_bias():
    """Почему обе метрики нужны одновременно.

    Недосчёт на одном ролике и такой же перебор на другом дают нулевое
    смещение при ненулевой погрешности. Обратный случай — систематический
    недосчёт — даёт равные по модулю смещение и погрешность. Одна метрика ни
    того, ни другого не различает.
    """
    compensating = count_quality([(7, 12), (17, 12)])
    assert compensating.bias == 0.0
    assert compensating.error > 0

    systematic = count_quality([(7, 12), (7, 12)])
    assert systematic.bias == -systematic.error


def test_tolerance_shares_are_nested():
    """±1 не может быть строже, чем «точно», а ±2 — чем ±1."""
    q = count_quality([(5, 5), (6, 5), (7, 5), (9, 5)])
    assert q.exact == 0.25
    assert q.within_1 == 0.5
    assert q.within_2 == 0.75


def test_bias_is_undefined_when_nothing_happened():
    """Смещение относительно нуля не определено — и не должно выдумываться.

    Доли при этом считаются: «ничего не произошло, и мы ничего не насчитали» —
    полноценный проверяемый результат.
    """
    q = count_quality([(0, 0), (0, 0)])
    assert math.isnan(q.bias)
    assert math.isnan(q.error)
    assert q.exact == 1.0


def test_empty_input_does_not_crash():
    q = count_quality([])
    assert q.units == 0
    assert math.isnan(q.exact)
    assert math.isnan(q.bias)


def test_mae_is_per_unit():
    q = count_quality([(7, 12), (5, 5)])
    assert q.mae == 2.5


# ---- Разделение по домену -------------------------------------------------
#
# Целевой сценарий — статичная камера на штативе, ТС целиком в кадре. Часть
# набора ему не отвечает: метро Гаосюна 640×480 взято ради плотной толпы,
# съёмка с рук — заведомо негативный случай. Смешивать их в одну цифру значит
# оценивать систему по материалу, для которого она не предназначена, и наоборот
# — прятать провалы на целевых сценах за чужими роликами.


def test_domains_are_reported_separately():
    rows = [
        (TARGET_DOMAIN, 1, 1),
        (TARGET_DOMAIN, 0, 0),
        ("отладочный", 7, 12),
    ]
    q = quality_by_domain(rows)

    assert q[TARGET_DOMAIN].units == 2
    assert q[TARGET_DOMAIN].error == 0.0
    assert q["отладочный"].error > 0


def test_whole_set_is_reported_alongside_domains():
    """Общая цифра остаётся: она сопоставима с прошлыми замерами."""
    rows = [(TARGET_DOMAIN, 1, 1), ("отладочный", 7, 12)]
    q = quality_by_domain(rows)
    assert "весь набор" in q
    assert q["весь набор"].units == 2


def test_domain_split_can_hide_a_failure_and_that_is_the_point():
    """Ровно то, ради чего разделение и вводится.

    Идеальный счёт на целевых сценах и провал на отладочных дают среднюю
    цифру, по которой нельзя принять ни одного решения: она хуже, чем есть на
    целевом домене, и лучше, чем есть на трудном.
    """
    rows = [(TARGET_DOMAIN, 5, 5), (TARGET_DOMAIN, 3, 3), ("отладочный", 0, 10)]
    q = quality_by_domain(rows)

    assert q[TARGET_DOMAIN].error == 0.0
    assert q["отладочный"].error == 1.0
    assert 0.0 < q["весь набор"].error < 1.0


def test_empty_rows_give_empty_result():
    assert quality_by_domain([]) == {}


def test_typo_in_domain_is_caught():
    """Опечатка обязана падать, а не заводить третий домен молча.

    Без проверки «целевои» вместо «целевой» дал бы ещё одну строку в отчёте с
    одним видео внутри, а целевой домен незаметно похудел бы на это видео.
    Ровно так уже терялись ошибки разметки дверей.
    """
    assert unknown_domains(["целевой", "отладочный"]) == set()
    assert unknown_domains(["целевои"]) == {"целевои"}
    assert unknown_domains(["целевой", ""]) == {""}


def test_known_domains_include_the_target_one():
    assert TARGET_DOMAIN in KNOWN_DOMAINS
