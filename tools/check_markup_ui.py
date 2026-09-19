"""Проверка экрана разметки живым браузером: то, чего не видят обычные тесты.

    uv run paxcount serve &
    uv run --group ui python tools/check_markup_ui.py

Проверяются инварианты, каждый из которых уже был нарушен на живом экране:

* **страница не выше окна.** Панель-флексбокс без `min-height:0` требует высоту
  всего списка записей, страница вырастает до трёх тысяч пикселей, а полотно и
  нижняя панель уезжают за край. Снаружи это выглядит как «экран не открылся»;
* **карточка мастера целиком в окне** на каждом шаге и при любом состоянии
  панелей — иначе подсказка показывает на то, чего не видно;
* **свёрнутая панель оставляет кнопку** — панель, которую нечем вернуть, это не
  сворачивание, а потеря;
* **состояние панелей переживает перезагрузку**.

Скриншоты не сохраняются: на экране боевые кадры с лицами и номерами (запрет 6).
"""

from __future__ import annotations

import sys

URL = "http://127.0.0.1:8000/markup"
VIEWPORTS = ((1400, 860), (1100, 700))


def check(page, size) -> list[str]:
    bad: list[str] = []
    page.goto(URL)
    page.wait_for_timeout(900)
    page.evaluate("localStorage.clear()")
    page.reload()
    page.wait_for_timeout(900)
    page.keyboard.press("Escape")
    page.wait_for_timeout(200)

    height = page.evaluate("document.body.scrollHeight")
    if height > page.evaluate("innerHeight") + 1:
        bad.append(f"{size}: страница выше окна ({height} px) — полотно уедет за край")

    for collapsed in (False, True):
        if collapsed:
            page.click("#righttoggle")
            page.click("#lefttoggle")
            page.wait_for_timeout(250)
            for side, hidden in (("left", "#files"), ("right", "#doors")):
                if not page.is_visible(f"#{side}toggle"):
                    bad.append(f"{size}: свёрнутая панель {side} не оставила кнопки")
                if page.is_visible(hidden):
                    bad.append(f"{size}: панель {side} свёрнута, а содержимое видно")
        page.click("#helpbtn")
        page.wait_for_timeout(400)
        for step in range(1, 30):
            box = page.evaluate(
                "() => { const c = document.getElementById('wizcard')"
                ".getBoundingClientRect();"
                " return [c.left, c.top, c.right, c.bottom, innerWidth, innerHeight]; }"
            )
            left, top, right, bottom, vw, vh = box
            if left < 0 or top < 0 or right > vw + 1 or bottom > vh + 1:
                bad.append(f"{size}, панели свёрнуты={collapsed}: карточка мастера на "
                            f"шаге {step} за краем окна")
            if page.inner_text("#wiznext") == "Начать":
                break
            page.evaluate("document.getElementById('wiznext').click()")
            page.wait_for_timeout(350)
        page.keyboard.press("Escape")
        page.wait_for_timeout(250)

    bad += check_seek(page, size)
    bad += check_playback_size(page, size)

    # Сворачиваем заново перед проверкой памяти: мастер разворачивает панели
    # сам, когда шаг показывает на свёрнутое, и это правильно — проверять
    # память после него значило бы проверять не то.
    for side in ("left", "right"):
        if not page.evaluate(f"document.body.classList.contains('{side}-off')"):
            page.click(f"#{side}toggle")
    page.wait_for_timeout(250)
    page.reload()
    page.wait_for_timeout(900)
    columns = page.evaluate("getComputedStyle(document.body).gridTemplateColumns")
    if not columns.startswith("30px") or not columns.endswith("30px"):
        bad.append(f"{size}: свёрнутые панели не пережили перезагрузку ({columns})")
    return bad


def check_seek(page, size) -> list[str]:
    """Полоса записи переносит в точку, по которой щёлкнули.

    Проигрывание нашло машину не там, где ждали, — нужно вернуться на пять
    минут назад, и перематывать эти пять минут нечем: поля «кадр» и «время»
    убраны вместе с нижней плашкой. Поэтому полоса просмотренного не только
    показывает позицию, но и принимает щелчок.

    Проверяется живым браузером, а не тестом: попадание мышью в полосу
    высотой в несколько пикселей — свойство вёрстки, и юнит-тест его не видит.
    """
    bad: list[str] = []
    page.click("aside .file")           # первый файл списка
    page.wait_for_timeout(2500)
    if not page.evaluate("!!S.file"):
        return [f"{size}: файл не открылся, проверить переход по полосе нечем"]

    box = page.evaluate(
        "() => { const r = document.getElementById('seen').getBoundingClientRect();"
        " return [r.left, r.top, r.width, r.height]; }"
    )
    left, top, width, height = box
    if height < 6:
        bad.append(f"{size}: полоса записи высотой {height} px — в неё не попасть мышью")

    page.mouse.click(left + width * 0.75, top + height / 2)
    page.wait_for_timeout(1200)
    part = page.evaluate("S.frame / (S.file.frames - 1)")
    if not 0.70 < part < 0.80:
        bad.append(f"{size}: щелчок по трём четвертям полосы привёл на {part:.0%} записи")

    page.mouse.click(left + width * 0.1, top + height / 2)
    page.wait_for_timeout(1200)
    part = page.evaluate("S.frame / (S.file.frames - 1)")
    if not 0.05 < part < 0.15:
        bad.append(f"{size}: щелчок по десятой доле полосы привёл на {part:.0%} записи")
    return bad


def check_playback_size(page, size) -> list[str]:
    """Кадр не меняет размера на проигрывании.

    На ходу кадр запрашивается ужатым — так он успевает прийти к сроку. Но
    рисовался он по собственной ширине, и картинка на пуске скакала вдвое
    меньше, а на остановке возвращалась. Смотреть на это нельзя: глаз ловит
    прыжок, а не машину в кадре.

    Хуже другое, невидимое: к той же ширине привязаны координаты разметки.
    Кадр записи 1920 px, превью 1280 — обведённая на превью дверь уехала бы в
    полтора раза.
    """
    bad: list[str] = []
    page.evaluate("showFrame(600)")
    page.wait_for_timeout(1200)
    было = page.evaluate("[S.view.k, frameW(), frameH()]")

    page.click("[data-play='10']")
    page.wait_for_timeout(1500)
    стало = page.evaluate("[S.view.k, frameW(), frameH()]")
    page.keyboard.press(" ")
    page.wait_for_timeout(800)

    if стало != было:
        bad.append(f"{size}: на проигрывании кадр сменил размер {было} -> {стало}")
    return bad


def main() -> int:
    from playwright.sync_api import sync_playwright

    problems: list[str] = []
    errors: list[str] = []
    with sync_playwright() as pw:
        browser = pw.chromium.launch()
        for width, height in VIEWPORTS:
            page = browser.new_page(viewport={"width": width, "height": height})
            page.on("pageerror", lambda e: errors.append(str(e)))
            try:
                problems += check(page, f"{width}x{height}")
            finally:
                page.close()
        browser.close()

    for line in problems + [f"ошибка на странице: {e}" for e in errors]:
        print(f"✗ {line}")
    if problems or errors:
        return 1
    print(f"✓ экран разметки: проверено {len(VIEWPORTS)} размера окна, замечаний нет")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
