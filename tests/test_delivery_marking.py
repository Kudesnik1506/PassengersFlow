"""Что в книге красится жёлтым: правило, а не привычка сборщика.

Пометка отвечает на один вопрос проверяющего: где смотреть кадр. Поэтому она
устроена по величине утверждения, а не по числу незаполненных граф:

* **записи у оператора нет вовсе** — спорна вся строка целиком. Машину видели
  только мы, второго свидетельства у неё нет ни в одном поле, и красить одну
  графу из пятнадцати значило бы указать не туда;
* **запись есть, разошлось одно поле** — красится ровно эта клетка. Залить из-за
  неё всю машину значит обесценить пометку: проверяющий станет искать спор в
  четырнадцати графах, где его нет;
* **всё сошлось** — не красится ничего.

Время занимает две графы бланка (часы и минуты), поэтому одно расхождение по
времени даёт две клетки: половина момента на белом фоне читалась бы как
«минуты сошлись», чего сверка не утверждала.
"""

from __future__ import annotations

from datetime import datetime, timedelta

from paxcount.delivery.agreement import Agreement, agree
from paxcount.delivery.fill import COLUMNS
from paxcount.delivery.marking import marks_for
from paxcount.delivery.model import DeliveryRow, VehicleKind, VehicleSize
from paxcount.delivery.operator import OperatorRecord


def record(**kw) -> OperatorRecord:
    base = dict(created=datetime(2026, 9, 10, 6, 54, 59), stop="22739",
                 kind="Автобус", route="50", size="Особо большой", board="1596",
                 occupancy="А", row=3)
    return OperatorRecord(**{**base, **kw})


def row(**kw) -> DeliveryRow:
    base = dict(group="697", date="10.09.2026", hours=7, minutes=2, stop="22739",
                 kind=VehicleKind.BUS, board_number="1596", state_number="А000АА00",
                 route="50", size=VehicleSize.XLARGE, alighted=0, boarded=5,
                 video="запись", operator="Иванов Иван")
    return DeliveryRow(**{**base, **kw})


SHIFT = timedelta(minutes=7, seconds=18)


def test_a_vehicle_the_operator_never_recorded_is_marked_whole():
    """Машину видели только мы — спорна вся строка, а не какая-то её графа."""
    assert marks_for(Agreement(None, frozenset())) == set(COLUMNS)


def test_a_single_disagreeing_field_marks_only_its_own_cell():
    """Разошлось одно поле — красится одна клетка, остальные четырнадцать нет."""
    result = agree(row(route="26"), [record()], SHIFT)
    assert result.mismatched == {"route"}
    assert marks_for(result) == {"H"}


def test_a_time_disagreement_marks_both_hours_and_minutes():
    """Момент лежит в двух графах: пометить одну значит сказать лишнее."""
    result = agree(row(hours=7, minutes=20), [record()], SHIFT)
    assert marks_for(result) == {"C", "D"}


def test_a_row_that_agrees_is_not_marked_at_all():
    """Сошлось — пометки нет. Иначе цвет перестаёт что-либо значить."""
    assert marks_for(agree(row(), [record()], SHIFT)) == set()


def test_a_missing_occupancy_is_not_a_disagreement():
    """Наполненность мы не считаем вовсе (решение 032) — спорить в ней нечем."""
    result = agree(row(), [record(occupancy=None)], SHIFT)
    assert "I" not in marks_for(result)


# ---- Наша правка чужой строки ------------------------------------------------
#
# Строка оператора, которую мы не расшифровывали, не красится: она целиком его.
# Но если мы в ней что-то ИСПРАВИЛИ — эта клетка уже наша, и она обязана быть
# видна. Иначе заказчик, сверяя книгу с выгрузкой, найдёт расхождение и не
# поймёт, чьё оно: наша это правка или его собственная опечатка.

def test_a_field_we_repaired_is_marked():
    from paxcount.delivery.marking import marks_for_repair
    assert marks_for_repair(frozenset({"board", "route"})) == {"G", "H"}


def test_nothing_repaired_marks_nothing():
    from paxcount.delivery.marking import marks_for_repair
    assert marks_for_repair(frozenset()) == set()


def test_a_duplicate_press_marks_the_whole_row():
    """Повтор — утверждение обо всей строке, а не о какой-то её графе.

    Лишняя здесь не ячейка, а сама машина: её записали дважды. Покрасить одну
    графу значило бы сказать, что спорна она, — и отправить искать ошибку в
    номере или маршруте, где всё верно.
    """
    from paxcount.delivery.marking import marks_for_duplicate
    assert marks_for_duplicate() == set(COLUMNS)


# ---- Наш счёт в чужой строке -------------------------------------------------
#
# Строка машины, которая нашлась у оператора, выглядит как все остальные: вид,
# маршрут, размер, борт — его. Наше в ней только «Вышло» и «Зашло», и в ленте из
# трёхсот строк эти две клетки не найти. А ведь ради них всё и делается.

def test_our_count_is_marked_in_the_operators_row():
    """Счёт — наше измерение, и в книге он обязан быть виден.

    Графы переехали: K и L теперь заказчика, он заполняет их рукой
    (решение 085), а наш счёт стоит в Q и R. Метится то, где счёт лежит, —
    графы заказчика мы не красим, потому что не заполняем.
    """
    from paxcount.delivery.marking import marks_for_our_measurement
    marks = marks_for_our_measurement(row())
    assert {"Q", "R"} <= marks
    assert not ({"K", "L"} & marks), "чужую графу не метим: мы её не трогали"


def test_the_decoder_name_is_marked_as_ours():
    """Графа O — наша: у оператора на остановке нет ни фамилии, ни графы.

    Принцип 9 мерит происхождение, а не спор, и исключений в нём нет. Эта
    графа была единственной нашей без пометки.
    """
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "O" in marks_for_our_measurement(row(operator="Расшифровщик"))


def test_an_empty_decoder_name_is_not_marked():
    """Метится заполненная графа, а не сама возможность её заполнить."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "O" not in marks_for_our_measurement(row(operator=""))


def test_the_disagreement_column_is_marked_as_ours():
    """Графа S целиком наша: у оператора её нет, и спорит в ней только он с нами."""
    from paxcount.delivery.marking import marks_for_our_measurement
    disputed = row(disagreements={"размер": "по таблице 3 такой пары не бывает"})
    assert "S" in marks_for_our_measurement(disputed)
    assert "S" not in marks_for_our_measurement(row())


def test_a_row_without_a_count_is_not_marked():
    """Не считали — графы счёта не метятся: N/A не измерение.

    Прочие наши графы при этом метятся по-прежнему: имя файла и камера
    вычислены нами и без счёта.
    """
    from paxcount.delivery.marking import marks_for_our_measurement
    marks = marks_for_our_measurement(row(alighted=None, boarded=None))
    assert "K" not in marks and "L" not in marks


def test_the_comment_code_is_ours_too():
    """Код таблицы 2 выведен из нашей разметки дверей, а не взят у оператора."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "M" in marks_for_our_measurement(row(comment=7))


def test_the_camera_reading_is_marked_as_ours():
    """Графы P у оператора нет вовсе — она наша целиком (принцип 9).

    Помечается не потому, что мы с ним разошлись, а потому, что содержимое
    получено нами: проверяющий обязан отличать наше от его, не сверяясь с
    подписью столбца.
    """
    from datetime import datetime

    from paxcount.delivery.marking import marks_for_our_measurement
    seen = row(camera="3", camera_ts=datetime(2026, 8, 10, 6, 58, 6))
    assert "P" in marks_for_our_measurement(seen)


def test_an_empty_camera_reading_is_not_marked():
    """Посадку мы не видели — графа пуста, и красить в ней нечего."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "P" not in marks_for_our_measurement(row())


def test_the_video_file_name_is_ours_too():
    """Графы с именем файла у оператора нет — имя вычислили мы (принцип 9).

    В выгрузке нет ни файла, ни камеры: он жмёт кнопку на остановке. Значит
    графа N — наше утверждение «машину искать здесь», и проверяющий обязан
    видеть, что оно наше.
    """
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "N" in marks_for_our_measurement(row(video="запись"))


def test_an_empty_video_column_is_not_marked():
    """Записи нет — утверждать нечего, красить нечего."""
    from paxcount.delivery.marking import marks_for_our_measurement
    assert "N" not in marks_for_our_measurement(row(video=""))


def test_a_note_about_a_recording_gap_is_marked_like_any_other_comment():
    """Графа комментария метится по СОДЕРЖИМОМУ, а не по коду таблицы 2.

    Разрыв записи попадает в ту же графу словами, кода у него нет — и метка,
    смотревшая на код, его пропускала. Между тем сказали это мы: у оператора
    нет ни камер, ни их разрывов.
    """
    from paxcount.delivery.marking import marks_for_our_measurement
    noted = row(comment=None, notes=("разрыв записи камеры 2 07:33:45-07:51:39",))
    assert "M" in marks_for_our_measurement(noted)


def test_a_shifted_minute_marks_the_time_of_an_operators_row():
    """Поправка часов переставила минуту — клетки времени наши и красятся.

    Строку оператора мы не расшифровывали, но время в ней не его: оно
    переведено на общую шкалу измеренной поправкой (решение 067). Там, где
    поправка перевалила через минуту, в графах C и D стоит НАШЕ число, и оно
    отличается от того, что записал оператор, — принцип 9 исключений не знает.
    """
    from paxcount.delivery.marking import marks_for_shifted_time
    written = record(created=datetime(2026, 9, 10, 6, 54, 59))
    # 06:54:59 + 18 с = 06:55:17 — оператор записал 54-ю минуту, в книге 55-я.
    assert marks_for_shifted_time(row(hours=6, minutes=55), written) == {"C", "D"}


def test_a_shift_inside_the_same_minute_marks_nothing():
    """Минута не сдвинулась — в графах то же число, что у оператора."""
    from paxcount.delivery.marking import marks_for_shifted_time
    written = record(created=datetime(2026, 9, 10, 6, 54, 10))
    # 06:54:10 + 18 с = 06:54:28 — обе графы совпадают с записью.
    assert marks_for_shifted_time(row(hours=6, minutes=54), written) == set()


def test_a_decoded_row_marks_time_even_when_the_check_sees_no_disagreement():
    """Наша строка: сверка спора не видит, а минута в графе всё равно чужая.

    Допуск сверки — минута (`agreement.TIME_TOLERANCE`), и расхождение в
    семнадцать секунд спором не считается: это свойство смены, а не машины.
    Но бланк хранит часы и минуты, и 06:54:59 у оператора против 06:55 в книге
    заказчик видит как разные минуты. Пометка здесь говорит не «мы спорим», а
    «это число наше» — принцип 9 мерит происхождение, а не спор.
    """
    from paxcount.delivery.marking import marks_for_shifted_time
    written = record(created=datetime(2026, 9, 10, 6, 54, 59))
    ours = row(hours=6, minutes=55)
    # Боевая поправка смены — 18 секунд: 06:54:59 + 18 с = 06:55:17 против
    # 06:55:00 в строке, то есть семнадцать секунд, а допуск сверки — минута.
    measured = timedelta(seconds=18)
    assert agree(ours, [written], measured).mismatched == frozenset()
    assert marks_for_shifted_time(ours, written) == {"C", "D"}


# --- строка, найденная цепочкой по камере 1 -----------------------------------

def test_a_chain_row_is_yellow_all_the_way():
    """Машину видели только мы — спорна вся строка, а не отдельная графа.

    Записи оператора у неё нет вовсе, второго свидетельства нет ни в одном
    поле: та же величина утверждения, что у строки без записи (решение 070).
    Покрасить в ней одну графу значило бы указать проверяющему не туда.
    """
    from paxcount.delivery.marking import marks_for_chain

    assert marks_for_chain() == set(COLUMNS)


def test_a_corrected_row_is_marked_whole():
    """Исправление маршрута метит строку целиком, а не одну графу.

    Спорна не клетка: у машины оказалось два нажатия с разными маршрутами, и
    сообщение читателю — «в этой строке мы приняли поправку оператора». Цвет
    у неё свой, отличный и от спора, и от лишней строки.
    """
    from paxcount.delivery.marking import marks_for_correction

    assert marks_for_correction() == set(COLUMNS)
