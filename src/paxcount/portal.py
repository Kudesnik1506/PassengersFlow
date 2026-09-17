"""Транспортный портал Санкт-Петербурга: бортовой номер → госномер и маршрут.

Дорога односторонняя, и это не оплошность клиента, а свойство портала: поиск по
`stateNumber` не находит ничего даже для номера, который портал сам же вернул,
а выгрузки парка за день нет вовсе (решение 026). Поэтому единственный вход —
бортовой (в терминах портала «парковый») номер.

Зачем оно нужно: инструкция требует писать автобусам государственный номер, а
оператор записывает бортовой для всех ТС. Замена и есть работа этого модуля.
Троллейбусам и трамваям замена не нужна — у них госномера нет, портал
возвращает в этой графе тот же бортовой.

Сеть внедряется снаружи (`transport`) — так тесты идут без обращения к
государственному порталу, а записанные ответы с реальными номерами и телефонами
перевозчиков не попадают в публичный репозиторий.
"""

from __future__ import annotations

import json
import time
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta
from http.cookiejar import CookieJar
from pathlib import Path
from typing import Callable, Protocol

from .delivery.model import KINDS_NEEDING_STATE_NUMBER, VehicleKind

BASE = "https://portal.gpt.adc.spb.ru/Portal/transport"
LOGIN_PATH = "/login/authenticate"
SEARCH_PATH = "/personal/vehicleSearch/list"

# Колонки таблицы результатов. Портал отвечает 500, если не прислать список:
# он собран не для нас, а для DataTables на его собственной странице.
COLUMNS = "parkNumber,stateNumber,transportOrg,transportType,route,workStarted,workFinished"

# Коды видов транспорта в форме портала.
PORTAL_TYPE = {
    VehicleKind.BUS: 0,
    VehicleKind.MINIBUS: 0,
    VehicleKind.SHUTTLE_WORK: 0,
    VehicleKind.SHUTTLE_MALL: 0,
    VehicleKind.TROLLEY: 1,
    VehicleKind.TRAM: 2,
}

# Пауза между запросами. Портал государственный, доступ общий на нескольких
# расшифровщиков — спешить здесь некуда, а 130 уникальных ТС за смену
# укладываются в пару минут даже с паузой.
REQUEST_PAUSE_S = 1.2

# Заголовки живого запроса. Вынесены в константу, чтобы их проверял тест:
# HTTP кодирует заголовки в latin-1, и кириллица здесь роняет запрос ещё на
# отправке. Подставной транспорт такого не ловит — поймал живой портал.
HTTP_HEADERS = [
    ("X-Requested-With", "XMLHttpRequest"),
    ("User-Agent", "paxcount/0.1 (passenger flow survey)"),
]


class Transport(Protocol):
    def __call__(self, path: str, fields: dict) -> dict: ...


@dataclass(frozen=True)
class VehicleInfo:
    """Что портал знает о машине на конкретную дату — в одной её смене.

    `shifts_total` и `selected_by` существуют, чтобы выбор был проверяем.
    У каждой восьмой машины парка за день две смены на разных маршрутах, и
    молчаливый выбор одной из них неотличим от отсутствия выбора.
    """

    board_number: str
    state_number: str | None
    route: str | None
    carrier: str | None
    shift_start: str | None
    shift_end: str | None
    # Сколько смен портал вернул всего и чем руководствовались, выбирая.
    shifts_total: int = 1
    selected_by: str = "первая строка"


@dataclass(frozen=True)
class NotFound:
    """Машины нет в базе портала на эту дату.

    Отдельный тип, а не `None`: в принятой таблице заказчика есть строка
    «34547 -нет в базе» — пробелы в базе реальны и у людей. Молча вернуть
    пустоту значит потерять машину и выдать неполный файл за полный.
    """

    board_number: str
    reason: str


Answer = VehicleInfo | NotFound


def credentials(env_path: Path = Path(".env")) -> tuple[str, str]:
    """Учётка из окружения, иначе из .env. В коде её нет и быть не должно.

    Переменные окружения имеют приоритет: так запуск в CI или на чужой машине
    не требует класть файл с паролем на диск.
    """
    import os

    login = os.environ.get("PORTAL_LOGIN")
    password = os.environ.get("PORTAL_PASSWORD")
    if login and password:
        return login, password
    if not env_path.exists():
        raise RuntimeError(
            f"нет учётки портала: ни PORTAL_LOGIN/PORTAL_PASSWORD в окружении, "
            f"ни файла {env_path}. Образец — .env.example"
        )
    values: dict[str, str] = {}
    for line in env_path.read_text(encoding="utf-8").splitlines():
        if "=" in line and not line.strip().startswith("#"):
            key, _, value = line.partition("=")
            values[key.strip()] = value.strip()
    try:
        return values["PORTAL_LOGIN"], values["PORTAL_PASSWORD"]
    except KeyError as exc:
        raise RuntimeError(f"в {env_path} нет {exc.args[0]}") from exc


def http_transport() -> Transport:
    """Настоящая сеть: сессия на куках, как у браузера на странице портала."""
    jar = CookieJar()
    opener = urllib.request.build_opener(urllib.request.HTTPCookieProcessor(jar))
    opener.addheaders = list(HTTP_HEADERS)
    # Страница ставит сессионную куку до входа — без неё вход не закрепляется.
    opener.open(f"{BASE}/personal/vehicleSearch", timeout=30).read()

    def post(path: str, fields: dict) -> dict:
        data = urllib.parse.urlencode(fields, encoding="utf-8").encode()
        with opener.open(BASE + path, data, timeout=30) as response:
            return json.loads(response.read().decode("utf-8", "replace"))

    return post


class PortalClient:
    """Поиск ТС по бортовому номеру с кэшем и паузой между запросами."""

    def __init__(
        self,
        transport: Transport,
        login: str,
        password: str,
        sleep: Callable[[float], None] = time.sleep,
        pause_s: float = REQUEST_PAUSE_S,
    ):
        self._transport = transport
        self._login = login
        self._password = password
        self._sleep = sleep
        self._pause_s = pause_s
        self._authenticated = False
        # Ключ — борт и дата: одна машина в разные дни ходит по разным
        # маршрутам, и ответ за вторник не годится для среды. Хранятся
        # **сырые строки**, а не разобранный ответ: выбор смены зависит от
        # времени прибытия, и разбирать надо на каждый вызов, а вот ходить в
        # государственный портал — один раз.
        self._cache: dict[tuple[str, str], list] = {}

    def snapshot(self) -> dict:
        """Кэш ответов в виде, который переживает json: ключ — «борт|дата».

        Нужен между запусками: портал государственный, доступ общий, а книга
        пересобирается по многу раз за день. Двести бортов на прогон — двести
        одинаковых запросов к чужой системе.
        """
        return {f"{board}|{date}": rows for (board, date), rows in self._cache.items()}

    def preload(self, saved: dict) -> None:
        """Поднимает сохранённый кэш. Чужие ключи молча не берём — падаем."""
        for key, rows in saved.items():
            board, _, date = key.partition("|")
            if not date:
                raise ValueError(f"ключ кэша не в форме «борт|дата»: {key!r}")
            self._cache[(board, date)] = rows

    def _authenticate(self) -> None:
        if self._authenticated:
            return
        answer = self._transport(
            LOGIN_PATH, {"j_username": self._login, "j_password": self._password}
        )
        if not answer.get("success"):
            raise PermissionError(
                f"портал не принял учётку: {answer.get('result') or 'без объяснения'}"
            )
        self._authenticated = True

    def lookup(self, board_number: str, date: str, kind: VehicleKind,
               at: datetime | None = None) -> Answer:
        """Ищет машину по бортовому номеру на дату (ISO ГГГГ-ММ-ДД).

        `at` — момент прибытия на остановку. Он выбирает смену: борт 38182 в
        один и тот же день ходил по 64-му утром и по 481-му вечером, и без
        времени ответ был бы верен ровно наполовину.
        """
        key = (board_number, date)
        rows = self._cache.get(key)
        if rows is None:
            self._authenticate()
            self._sleep(self._pause_s)
            answer = self._transport(SEARCH_PATH, {
                "sEcho": 1, "iDisplayStart": 0, "iDisplayLength": 25, "iColumns": 7,
                "sColumns": COLUMNS,
                "parkNumber": board_number, "stateNumber": "",
                "searchDay": datetime.strptime(date, "%Y-%m-%d").strftime("%d.%m.%Y"),
                "transportType": PORTAL_TYPE[kind],
            })
            rows = answer.get("aaData") or []
            self._cache[key] = rows

        if not rows:
            return NotFound(
                board_number, f"ТС {board_number} нет в базе портала на {date}"
            )
        row, reason = _pick_shift(rows, at)
        return _parse(row, kind, shifts_total=len(rows), selected_by=reason)


def _pick_shift(rows: list, at: datetime | None) -> tuple[list, str]:
    """Выбирает смену под время прибытия и говорит, чем руководствовался.

    Время вне всех смен — не редкость: машина могла заехать на остановку по
    пути в парк. Тогда берётся ближайшая смена, но выбор помечается, чтобы
    строку можно было проверить, а не считать установленной.
    """
    if at is None:
        return rows[0], "первая строка"
    if len(rows) == 1:
        return rows[0], "единственная"

    spans = [(_span(row), row) for row in rows]
    for (start, end), row in spans:
        if start and end and start <= at <= end:
            return row, "время"

    def distance(item):
        (start, end), _ = item
        if start is None or end is None:
            return timedelta.max
        return min(abs(at - start), abs(at - end))

    return min(spans, key=distance)[1], "ближайшая"


def _span(row: list) -> tuple[datetime | None, datetime | None]:
    """Начало и конец смены. Конец раньше начала — значит смена ушла за полночь."""
    start, end = _moment(row[5] if len(row) > 5 else None), _moment(
        row[6] if len(row) > 6 else None
    )
    if start and end and end < start:
        end += timedelta(days=1)
    return start, end


def _moment(value: object) -> datetime | None:
    text = str(value or "").strip()
    for shape in ("%Y-%m-%d %H:%M", "%Y-%m-%d %H:%M:%S", "%d.%m.%Y %H:%M"):
        try:
            return datetime.strptime(text, shape)
        except ValueError:
            continue
    return None


def _parse(row: list, kind: VehicleKind, shifts_total: int = 1,
           selected_by: str = "первая строка") -> VehicleInfo:
    """Разбирает строку ответа портала.

    Госномер берётся только у тех видов ТС, у которых он бывает: в графе
    троллейбуса портал повторяет бортовой, и принять это за номерной знак
    значит записать заказчику «3144» там, где он ждёт номер вида А001АА198.
    """
    board = str(row[0] or "").strip()
    raw_state = str(row[1] or "").strip()
    state = raw_state if kind in KINDS_NEEDING_STATE_NUMBER else None
    if state and state == board:
        state = None  # портал повторил бортовой — номерного знака нет
    org = row[2] if isinstance(row[2], dict) else {}
    route = row[4] if isinstance(row[4], dict) else {}
    return VehicleInfo(
        board_number=board,
        state_number=state or None,
        route=str(route.get("routeNumber") or "").strip() or None,
        carrier=str(org.get("name") or "").strip() or None,
        shift_start=str(row[5] or "").strip() or None,
        shift_end=str(row[6] or "").strip() or None,
        shifts_total=shifts_total,
        selected_by=selected_by,
    )
