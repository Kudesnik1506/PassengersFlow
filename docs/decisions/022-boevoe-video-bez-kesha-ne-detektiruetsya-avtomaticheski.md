+++
{
  "title": "Автоматические пути (гейты, веб-UI) не детектируют боевое видео без кэша",
  "date": "2026-09-10",
  "kind": "решение",
  "authority": "уклад",
  "status": "действует",
  "tags": ["гейты", "кэш"],
  "verify": "test: tests/test_prod_workflow.py",
  "invalidates_on": ["появились боевые записи"],
  "evidence": [
    "file:src/paxcount/settings.py",
    "file:src/paxcount/baseline.py",
    "file:src/paxcount/webui/app.py",
    "file:tests/test_prod_workflow.py"
  ],
  "origin": "по ходу",
  "supersedes": []
}
+++

`baseline`, `doors jitter` и веб-UI не вызывают `get_tracks(video)` напрямую для
видео из `data/prod_videos/` — только через `settings.detector_for(video)` и
проверку `settings.prod_cache_missing(video)`, которая пропускает видео с
сообщением вместо запуска детекции.

## Почему

`pre-push` гоняет `doors jitter` и `baseline` по всем видео на каждый push,
включая боевые. Боевая запись — часы, а не секунды: `stride=1` на ней — это
многочасовая детекция внутри обычного `git push`. Та же дыра была в
`webui/app.py:_tracks` (искала кэш под настройками по умолчанию, для боевого
видео с `stride=3` всегда отвечала «кэша нет») и в `api_run` (мог начать
детекцию прямо внутри HTTP-запроса).

`detector_for` — единственный источник stride (3 для `prod_videos/`, 1 для
`test_videos/`), он же входит в ключ кэша через `DetectorSettings.tag()`.
Разъехавшиеся копии этого выбора и есть механизм дефекта: одно место кэш находит,
другое считает, что его нет, и запускает детекцию заново поверх готового кэша.

## Что отсюда следует

Не звать `get_tracks(video)` напрямую в новых автоматических путях (новые
гейты, CI-шаги, обработчики веб-UI) для видео, что могут прийти из
`prod_videos/` — всегда через `detector_for`/`prod_cache_missing`. Ручной запуск
(`paxcount run <видео>`) не ограничен — там пользователь сознательно ждёт часы.
