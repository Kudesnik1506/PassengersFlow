"""Счёт строки книги: на какой камере и в какой момент смотреть посадку.

Строка книги знает камеру и её часы (графа P), но не всякая камера видит
двери. К1 стоит в ста метрах выше по ходу и снимает проезд (решение 030), а
строки на ней появились ровно там, где К2 не писала: посадку такой машины
видно только на К3, и искать её надо по часам К3.

Часы камер сводятся через общую шкалу — поправками из `data/clocks`, как и
везде в сборке. У К3 в именах файлов август вместо сентября, поэтому момент
несёт дату файлов своей камеры, а не дату смены: иначе запись не найдётся.
"""

from __future__ import annotations

from datetime import date, datetime, timedelta

# Камеры, которые видят двери на стоянке. Порядок — порядок предпочтения:
# К2 снимает борт ближе, К3 — запасная, когда К2 молчит.
COUNTING = ("2", "3")


def on_camera(camera: str, moment: datetime, target: str,
               offsets: dict[str, float], days: dict[str, date]) -> datetime:
    """Тот же физический момент — на часах другой камеры."""
    common = moment - timedelta(seconds=offsets[camera])
    there = common + timedelta(seconds=offsets[target])
    return datetime.combine(days[target], there.time())


def on_counting_camera(cell: str, offsets: dict[str, float],
                        days: dict[str, date]) -> tuple[str, datetime] | None:
    """Счётная камера и её момент для строки по графе P («К2 08:20:20»).

    `None` — строка камеры не знает, и смотреть посадку негде.
    """
    parts = (cell or "").split()
    if len(parts) != 2 or not parts[0].startswith("К"):
        return None
    camera = parts[0][1:]
    clock = datetime.strptime(parts[1], "%H:%M:%S").time()
    moment = datetime.combine(days[camera], clock)
    if camera in COUNTING:
        return camera, moment
    target = COUNTING[-1]
    return target, on_camera(camera, moment, target, offsets, days)
