"""Сборка строки поставки из того, что удалось узнать о визите.

Главное правило здесь — правило владельца: **строка не пропадает из-за того,
что счёт не удался**. Машину видно, а людей посчитать нельзя — пишем маршрут,
номер, время, камеру и размер, а пустыми остаются ровно графы счёта. Обратное
(выбросить строку целиком) хуже вдвойне: заказчик сверяет число строк с
выгрузкой оператора, и пропавшая машина видна сразу, а «пустой счёт при
известной машине» — законный, предусмотренный инструкцией исход.

Как показывать незнание, решает слой записи, а не этот модуль: здесь незнание
— это `None`. В принятом заказчиком файле `N/A` стоит только в «Вышло» и
«Зашло» (по два раза на 312 строк), а неизвестное в остальных графах просто
пусто — подписывать словом «N/A» размер или маршрут значит разойтись с
образцом, который уже прошёл приёмку.

Коды комментария выводятся из разметки дверей, но только «посчитать нельзя»
(5-8). Коды 1-4 означают «дверь не в кадре, но людей всё же досчитали» — иным
способом: люди прошли под камерой, салон видно пустым. Знает это только тот,
кто их досчитал. Выставить 1-4 автоматически значило бы заявить о счёте,
которого не было.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import TYPE_CHECKING

from ..truth import DoorLayout
from .model import DOORS_BY_SIZE, DeliveryRow, VehicleKind, VehicleSize

if TYPE_CHECKING:  # только ради подсказки типа: coverage тянет timeline
    from .coverage import Gap


@dataclass(frozen=True)
class VisitFacts:
    """Всё, что известно о визите к моменту сборки строки. `None` — не знаем."""

    visit_key: str
    camera: str
    stop_ts: datetime
    layout: DoorLayout | None = None
    boarded: int | None = None
    alighted: int | None = None
    route: str | None = None
    board_number: str | None = None
    state_number: str | None = None
    kind: VehicleKind | None = None
    size: VehicleSize | None = None
    stood_where: str | None = None


def code_for_hidden(hidden: list[int], total: int) -> tuple[int | None, str | None]:
    """Код таблицы 2 по номерам невидимых дверей. Пара «код, пояснение».

    Отделено от `comment_code`, потому что номера невидимых дверей приходят с
    двух сторон: из ручной разметки (человек отметил дверь закрытой) и из счёта
    дверей в кадре (`delivery.edgedoors`). Таблица одна, и толковать её дважды
    значит однажды истолковать по-разному.

    Двери нумеруются от носа. `None` с пояснением — случай в таблицу 2 не
    укладывается, и решать его должен человек: зеркальный код уйдёт заказчику
    правдоподобным и неверным.
    """
    if not hidden:
        return None, None
    if len(hidden) == total:
        return None, (
            "ни одной двери не видно: таблица 2 такого случая не описывает — "
            "вопрос к заказчику, ставить код или N/A"
        )

    half = total // 2
    first_half = list(range(1, half + 1))
    second_half = list(range(total - half + 1, total + 1))

    if hidden == [1]:
        return 5, None
    if hidden == [total]:
        return 7, None
    if total == 3 and hidden == [1, 2]:
        return 6, None
    if total == 3 and hidden == [2, 3]:
        return 8, None
    if total >= 4 and hidden == first_half:
        return 6, None
    if total >= 4 and hidden == second_half:
        return 8, None

    return None, (
        f"невидимые двери {hidden} из {total}: таблица 2 описывает только "
        "первую дверь, первую половину, последнюю и последнюю половину — "
        "этот случай решает человек"
    )


def comment_code(layout: DoorLayout) -> tuple[int | None, str | None]:
    """Код таблицы 2 по набору невидимых дверей разметки. Пара «код, пояснение».

    Возвращает только 5-8 («посчитать нельзя») — см. докстринг модуля.
    """
    hidden = sorted(d.n_from_nose for d in layout.doors if not d.in_frame)
    total = DOORS_BY_SIZE.get(layout.size) or len(layout.doors)
    return code_for_hidden(hidden, total)


# Допуск, в пределах которого рамка считается упёршейся в край кадра. Детектор
# кладёт бок машины ровно на границу, но округление и сглаживание трека дают
# пиксель-другой отступа, и жёсткое равенство пропускало бы настоящий обрез.
#
# Сам вывод кода из обреза кузова отсюда убран (решение 082): обрез кузова не
# означает пропавшей двери, и на 155 стоянках из 239 не означал. Осталась
# величина — ею пользуется `edgedoors.cut_end`, чтобы назвать срезанный конец.
EDGE_TOLERANCE_PX = 2.0


def gap_note(camera: str, start: datetime, end: datetime) -> str:
    """Упоминание разрыва записи для графы комментария.

    Заказчик (17.09): аномалию отражать в комментариях, у разрыва называть
    камеру и время. Время — на той же шкале, что и время строки (К2, решение
    036), а не на часах самой камеры: проверяющий должен сразу видеть, попадает
    ли эта машина в названный промежуток, а для этого часы должны быть одни.
    """
    return (f"разрыв записи камеры {camera} "
            f"{start.strftime('%H:%M:%S')}-{end.strftime('%H:%M:%S')}")


def notes_for_visit(
    moment: datetime,
    gaps: "list[Gap]",
    window_s: float = 0.0,
) -> tuple[str, ...]:
    """Аномалии съёмки, задевающие этот визит, — для графы комментария.

    Разрыв попадает в заметки, если визит в него угодил или в него уходит.
    Второй случай не придирка: машина, вставшая за десять секунд до дыры,
    считается не до конца, и в отчёте это обязано быть видно, а не выглядеть
    обычной строкой с маленьким числом.

    Порога тут нет и не должно быть: границы задаёт сама стоянка (`window_s` —
    её длительность), а не выбранная на глаз величина.

    Разрыв чужой камеры тоже заметка. Он не отменяет счёт по той камере,
    которая машину видела (решение 037), но объясняет проверяющему и соседние
    пропавшие строки, и то, почему эта собрана по двум камерам из трёх.
    """
    end = moment + timedelta(seconds=max(0.0, window_s))
    notes = []
    for gap in sorted(gaps, key=lambda g: (g.camera, g.start)):
        gap_end = gap.start + timedelta(seconds=gap.duration_s)
        if moment <= gap_end and end >= gap.start:
            notes.append(gap_note(gap.camera, gap.start, gap_end))
    return tuple(notes)


def build_row(
    facts: VisitFacts,
    *,
    group: str,
    stop: str,
    video: str,
    operator: str,
    date: str | None = None,
    notes: tuple[str, ...] = (),
    camera_ts: datetime | None = None,
) -> DeliveryRow:
    """Строка бланка по фактам визита. Незнание не заполняется догадкой.

    `notes` — аномалии съёмки словами (разрыв записи, край смены). Приходят
    снаружи: этот модуль знает визит, но не знает карту покрытия смены.

    `camera_ts` — что показывали часы САМОЙ камеры в этот момент. Приходит
    отдельно потому, что `facts.stop_ts` к этой минуте уже переведён на общую
    шкалу, а перематывать запись будут по часам камеры (решение 036).
    """
    code = comment_code(facts.layout)[0] if facts.layout is not None else None
    return DeliveryRow(
        group=group,
        date=date or facts.stop_ts.strftime("%d.%m.%Y"),
        hours=facts.stop_ts.hour,
        minutes=facts.stop_ts.minute,
        stop=stop,
        kind=facts.kind or VehicleKind.BUS,
        board_number=facts.board_number,
        state_number=facts.state_number,
        route=facts.route,
        size=facts.size,
        boarded=facts.boarded,
        alighted=facts.alighted,
        comment=code,
        notes=notes,
        video=video,
        operator=operator,
        camera=facts.camera,
        camera_ts=camera_ts,
    )


# Подпись неопознанного маршрута по пункту 17 инструкции. Та же, что у отказа
# от счёта, и это не совпадение: в обоих случаях заказчик просит написать
# «нечего сказать» словами, а не оставить графу пустой.
UNKNOWN_ROUTE = "N/A"


def with_unnamed_route(row: DeliveryRow) -> DeliveryRow:
    """Пункт 17: маршрут не виден — «N/A», а номер ТС уходит в комментарий.

    Дословно: «Если не виден номер маршрута, то тоже ставим N/A, а в пустой
    графе справа в таблице, на этой же строчке пишем бортовой номер или
    госномер транспорта».

    Какая графа «пустая справа», видно по принятому заказчиком образцу: правее
    графы расшифровщика в нём не заполнено ничего, а графа комментария занята
    на шести строках из 312. Значит она.

    Номер идёт тот же, что в графе номера (`row.number`): госномер у автобуса,
    бортовой у рельсового. Назвать нечего — и писать нечего: выдуманный номер
    хуже пустой графы.
    """
    if (row.route or "").strip():
        return row
    number = row.number
    update: dict = {"route": UNKNOWN_ROUTE}
    if number:
        update["notes"] = row.notes + (f"маршрут не опознан, ТС {number}",)
    return row.model_copy(update=update)


def size_from_doors(doors_total: int | None) -> VehicleSize | None:
    """Размер по числу дверей — обратная сторона таблицы 3.

    Нужна, когда двери пересчитаны по другой камере на ходу: с борта, который
    на остановке виден одной дверью, на проезде видны все. Неизвестное число
    дверей остаётся неизвестным размером, а не «Большим» по умолчанию: у
    оператора «Большой» стоит в 246 строках из 312, и угадать им — значит
    почти всегда попасть, ни разу этого не проверив.
    """
    if doors_total is None:
        return None
    for size, count in DOORS_BY_SIZE.items():
        if count == doors_total:
            return size
    return None
