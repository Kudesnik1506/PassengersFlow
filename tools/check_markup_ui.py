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

Скриншоты не сохраняются: на экране боевые кадры с лицами и номерами (запрет 7).
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
