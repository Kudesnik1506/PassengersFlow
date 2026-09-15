"""Клиент транспортного портала: борт → госномер, маршрут, смена.

Сеть в тестах не трогается: транспорт внедряется снаружи. Записанные настоящие
ответы фикстурами быть не могут — там реальные госномера, телефоны и адреса
перевозчиков, а репозиторий публичный (решения 016 и 018). Поэтому фикстуры
синтетические, а форма ответа взята из живого запроса и зафиксирована здесь.

Что проверяется по существу:
- «нет в базе» — это ответ, а не пустота. В принятой таблице заказчика есть
  строка «34547 -нет в базе»: пробелы реальны, и молча потерять такую машину
  значит выдать неполный файл за полный.
- один борт спрашивается один раз. За смену 130 уникальных ТС на 312 строк,
  и портал государственный — лишних запросов он не заслужил.
- у троллейбуса госномера нет: портал возвращает в этой графе тот же бортовой,
  и принять его за номерной знак нельзя.
"""

from __future__ import annotations

import pytest
from paxcount.delivery.model import VehicleKind
from paxcount.portal import NotFound, PortalClient, VehicleInfo

BUS_ANSWER = {
    "aaData": [[
        "10001", "А001АА198",
        {"name": 'ООО "ПЕРЕВОЗЧИК"', "id": 1},
        {"name": "Автобус", "systemName": "bus", "id": 0},
        {"routeNumber": "100", "name": "А — Б", "id": 7},
        "2026-05-19 09:15", "2026-05-19 18:40",
    ]]
}
TROLLEY_ANSWER = {
    "aaData": [[
        "3144", "3144",
        {"name": 'СПб ГУП "Горэлектротранс"', "id": 2},
        {"name": "Троллейбус", "systemName": "trolley", "id": 1},
        {"routeNumber": "14", "name": "В — Г", "id": 9},
        "2026-05-19 06:00", "2026-05-19 15:00",
    ]]
}
EMPTY_ANSWER = {"aaData": []}


class FakeTransport:
    """Подставной транспорт: считает вызовы и отдаёт заготовленные ответы."""

    def __init__(self, answers: dict[str, dict] | None = None):
        self.calls: list[tuple[str, dict]] = []
        self.answers = answers or {}
        self.sleeps: list[float] = []

    def __call__(self, path: str, fields: dict) -> dict:
        self.calls.append((path, dict(fields)))
        if path.endswith("authenticate"):
            return {"result": None, "success": True}
        return self.answers.get(fields.get("parkNumber", ""), EMPTY_ANSWER)

    def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


@pytest.fixture
def client() -> tuple[PortalClient, FakeTransport]:
    transport = FakeTransport({"10001": BUS_ANSWER, "3144": TROLLEY_ANSWER})
    return PortalClient(transport=transport, sleep=transport.sleep,
                        login="кто-то", password="что-то"), transport


def test_bus_lookup_returns_plate_route_and_carrier(client):
    portal, _ = client
    info = portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    assert isinstance(info, VehicleInfo)
    assert info.state_number == "А001АА198"
    assert info.route == "100"
    assert info.carrier == 'ООО "ПЕРЕВОЗЧИК"'
    assert info.board_number == "10001"


def test_trolley_has_no_plate_even_though_portal_echoes_board(client):
    """Портал кладёт в графу госномера тот же бортовой — это не номерной знак."""
    portal, _ = client
    info = portal.lookup("3144", "2026-05-19", VehicleKind.TROLLEY)
    assert info.board_number == "3144"
    assert info.state_number is None
    assert info.route == "14"


def test_missing_vehicle_is_an_answer_not_silence(client):
    portal, _ = client
    result = portal.lookup("99999", "2026-05-19", VehicleKind.BUS)
    assert isinstance(result, NotFound)
    assert "99999" in result.reason


def test_login_happens_once_for_many_lookups(client):
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    portal.lookup("3144", "2026-05-19", VehicleKind.TROLLEY)
    logins = [c for c in transport.calls if c[0].endswith("authenticate")]
    assert len(logins) == 1


def test_same_board_asked_only_once(client):
    """130 уникальных ТС на 312 строк — повторы не должны идти в сеть."""
    portal, transport = client
    first = portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    second = portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    searches = [c for c in transport.calls if c[0].endswith("list")]
    assert len(searches) == 1
    assert first == second


def test_not_found_is_cached_too(client):
    """Отсутствие в базе — тоже ответ, переспрашивать его незачем."""
    portal, transport = client
    portal.lookup("99999", "2026-05-19", VehicleKind.BUS)
    portal.lookup("99999", "2026-05-19", VehicleKind.BUS)
    assert len([c for c in transport.calls if c[0].endswith("list")]) == 1


def test_different_date_is_a_different_question(client):
    """Машина ходит по разным маршрутам в разные дни — дата входит в ключ."""
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    portal.lookup("10001", "2026-05-20", VehicleKind.BUS)
    assert len([c for c in transport.calls if c[0].endswith("list")]) == 2


def test_date_is_sent_in_portal_format(client):
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    search = [c for c in transport.calls if c[0].endswith("list")][0]
    assert search[1]["searchDay"] == "19.05.2026"


def test_transport_type_mapped_to_portal_codes(client):
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    portal.lookup("3144", "2026-05-19", VehicleKind.TROLLEY)
    codes = [c[1]["transportType"] for c in transport.calls if c[0].endswith("list")]
    assert codes == [0, 1]


def test_scolumns_present_or_portal_answers_500(client):
    """Портал требует sColumns: без него ответ 500, а не пустой список."""
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    search = [c for c in transport.calls if c[0].endswith("list")][0]
    assert "parkNumber" in search[1]["sColumns"]


def test_pause_between_requests(client):
    """Портал государственный и общий на нескольких расшифровщиков."""
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    portal.lookup("3144", "2026-05-19", VehicleKind.TROLLEY)
    assert transport.sleeps and all(s > 0 for s in transport.sleeps)


def test_cached_answer_does_not_sleep(client):
    portal, transport = client
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    before = len(transport.sleeps)
    portal.lookup("10001", "2026-05-19", VehicleKind.BUS)
    assert len(transport.sleeps) == before


def test_failed_login_is_loud():
    transport = FakeTransport()

    def bad(path: str, fields: dict) -> dict:
        transport.calls.append((path, fields))
        return {"result": "Bad credentials", "success": False}

    portal = PortalClient(transport=bad, sleep=transport.sleep,
                          login="кто-то", password="не то")
    with pytest.raises(PermissionError):
        portal.lookup("10001", "2026-05-19", VehicleKind.BUS)


def test_http_headers_are_latin1_encodable():
    """Заголовки HTTP кодируются latin-1: кириллица роняет запрос на отправке.

    Подставной транспорт этого не видит — ошибка живёт только в настоящем
    клиенте, и нашлась она живым запросом к порталу.
    """
    from paxcount.portal import HTTP_HEADERS

    for name, value in HTTP_HEADERS:
        name.encode("latin-1")
        value.encode("latin-1")
