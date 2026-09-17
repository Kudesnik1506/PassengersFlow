"""Правила заказчика исполняемым кодом и сторож правдоподобия.

Разделение намеренное. `validate` — это правила, записанные в инструкции: их
нарушение заказчик увидит сам, и файл вернётся. `plausibility` — не правила, а
подозрения: числа, которые формально допустимы, но не похожи на смену.

Второе нужно, потому что допуск ЦТП нам неизвестен и известен не будет. Оценить
«попали ли мы в их допуск» нечем, но заметить, что прогон выдал вдвое больше
людей, чем бывает на живой остановке, можно и без допуска.
"""

from __future__ import annotations

import pytest
from paxcount.delivery.model import DeliveryRow, Occupancy, VehicleKind, VehicleSize
from paxcount.delivery.plausibility import REFERENCE, suspicions
from paxcount.delivery.validate import delivery_filename, validate


def sample(**over) -> DeliveryRow:
    base = dict(
        group="1317", date="2026-05-19", hours=7, minutes=0, stop="1715-15182",
        kind=VehicleKind.BUS, board_number="38099", state_number="Р512ОЕ198",
        route="481", occupancy=Occupancy.A, size=VehicleSize.LARGE,
        alighted=3, boarded=2,
        video="2026-05-19 - 06-59-54 - 1715-15182 - 01", operator="Иванов Иван",
    )
    base.update(over)
    return DeliveryRow(**base)


def texts(problems) -> str:
    return " | ".join(p.message for p in problems)


# ---- Правила инструкции ----------------------------------------------------


def test_clean_rows_pass():
    assert validate([sample()]) == []


def test_bus_without_state_number_is_an_error():
    """«Госномера пишем обязательно, так как это требования заказчика»."""
    problems = validate([sample(state_number=None)])
    assert problems and "госномер" in texts(problems).lower()


def test_trolley_without_state_number_is_fine():
    assert validate([sample(kind=VehicleKind.TROLLEY, board_number="3144",
                            state_number=None, route="14")]) == []


def test_unknown_route_within_five_percent_allowed():
    """«Количество транспорта с неопознанным номером маршрута не должно
    превышать 5% от всего транспорта на остановке»."""
    rows = [sample(minutes=i, state_number=f"Р{i:03d}ОЕ198") for i in range(20)]
    rows[0] = sample(minutes=0, state_number="Р000ОЕ198", route=None)
    assert validate(rows) == []


def test_unknown_route_above_five_percent_is_an_error():
    rows = [sample(minutes=i, state_number=f"Р{i:03d}ОЕ198") for i in range(20)]
    rows[0] = sample(minutes=0, state_number="Р000ОЕ198", route=None)
    rows[1] = sample(minutes=1, state_number="Р001ОЕ198", route=None)
    problems = validate(rows)
    assert problems and "5" in texts(problems)


def test_row_without_route_must_carry_number_in_comment_column():
    """Инструкция: если маршрут не виден, номер ТС пишется в графе справа."""
    problems = validate([sample(route=None, state_number=None)])
    assert any("номер" in p.message.lower() for p in problems)


def test_missing_video_name_is_an_error():
    assert validate([sample(video="")]) != []


def test_missing_operator_is_an_error():
    assert validate([sample(operator="")]) != []


def test_uncountable_comment_with_counts_is_not_flagged():
    """Коды 5-8 значат «не все двери видно», а не «нельзя посчитать вовсе».

    Принятый заказчиком файл (2026-05-19-1317-1715-15182.xlsx) это подтверждает
    напрямую: строки 36, 250, 258, 260 несут код 5, и во всех число по видимым
    дверям стоит в К и Л (5/1, 4/4, 4/2, 2/3); строки 85 и 95 с кодом 1 —
    аналогично (7/2, 17/2). Прежняя проверка отвергала ровно то, что заказчик
    сам сдаёт и принимает — код и число по видимым дверям это норма, а не
    расхождение, которое надо объяснять.
    """
    assert validate([sample(comment=5, alighted=4, boarded=1)]) == []
    assert validate([sample(comment=7, alighted=17, boarded=2)]) == []


def test_uncountable_comment_without_any_count_is_also_fine():
    """Код 5-8 плюс N/A (нет счёта вовсе) — тоже законный исход, не только число."""
    assert validate([sample(comment=6, alighted=None, boarded=None)]) == []


def test_duplicate_vehicle_in_same_minute_is_flagged():
    """Одна машина дважды в одну минуту — почти наверняка двойной визит."""
    rows = [sample(), sample()]
    problems = validate(rows)
    assert any("дважды" in p.message.lower() or "повтор" in p.message.lower()
               for p in problems)


# ---- Имя файла -------------------------------------------------------------


def test_delivery_filename_matches_accepted_example():
    """Принятый заказчиком файл назван 2026-05-19-1317-1715-15182.xlsx."""
    assert delivery_filename("2026-05-19", "1317", "1715-15182") == \
        "2026-05-19-1317-1715-15182.xlsx"


# ---- Сторож правдоподобия --------------------------------------------------


def test_normal_shift_raises_no_suspicion():
    """Похожая на правду смена: разброс как у заказчика, включая пустые заезды."""
    rows = [
        sample(alighted=0 if i % 12 == 0 else 4,
               boarded=0 if i % 6 == 0 else 3,
               minutes=i % 60, hours=7 + i // 60)
        for i in range(40)
    ]
    assert suspicions(rows) == []


def test_double_the_usual_flow_is_suspicious():
    rows = [sample(alighted=0 if i % 12 == 0 else 20,
                   boarded=0 if i % 6 == 0 else 15,
                   minutes=i % 60, hours=7 + i // 60)
            for i in range(40)]
    assert suspicions(rows), "вдвое больше людей, чем бывает на остановке"


def test_no_zeros_at_all_is_suspicious():
    """На живой остановке пустые заезды есть: у заказчика 26 нулей на 310 строк.

    Их полное отсутствие — признак модели, которая всегда что-нибудь находит.
    """
    rows = [sample(alighted=3, boarded=2, minutes=i % 60, hours=7 + i // 60)
            for i in range(40)]
    assert any("нул" in s.message.lower() for s in suspicions(rows))


def test_reference_comes_from_the_accepted_table():
    """Ориентир — принятая работа, а не выдуманные числа."""
    assert REFERENCE.mean_alighted == pytest.approx(4.3, abs=0.1)
    assert REFERENCE.mean_boarded == pytest.approx(2.7, abs=0.1)
    assert REFERENCE.zero_alighted_share == pytest.approx(0.084, abs=0.01)


def test_suspicions_do_not_block_empty_input():
    assert suspicions([]) == []
