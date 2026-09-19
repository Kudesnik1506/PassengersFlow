"""Без чего сборка книги не начинается вовсе.

Требование владельца (18.09): группа ОП, номер ОП, направление камер, выгрузка
оператора и видеозаписи спрашиваются, а не угадываются. Умолчаний у них нет и
быть не должно — молчаливая подстановка здесь уже стоила поставки: в книгу
ушли чужая группа ОП и чужая фамилия расшифровщика, взятые из тестовой
заготовки.

Отказ называет, ЧЕГО не хватает, словами заказчика, а не именами ключей: читать
его будет человек, который держит перед глазами папку с работой, а не наш код.

ФИО в этот список не входит: расшифровщик всегда один и тот же, и его имя лежит
в настройке рядом с учёткой портала — в публичный репозиторий оно не попадает
(решение 016).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from paxcount.delivery.inputs import Inputs, decoder_name, missing


def complete(tmp_path: Path, **over) -> Inputs:
    export = tmp_path / "выгрузка.xlsx"
    export.write_bytes(b"x")
    videos = tmp_path / "видео"
    videos.mkdir(exist_ok=True)
    template = tmp_path / "шаблон.xlsx"
    template.write_bytes(b"x")
    base = dict(group="697", stop="22739", operator_export=export, videos=videos,
                 template=template, cameras=("2", "3"))
    return Inputs(**{**base, **over})


def test_a_complete_set_starts_the_work(tmp_path):
    assert missing(complete(tmp_path)) == []


@pytest.mark.parametrize("field,value", [
    ("group", ""),
    ("stop", ""),
    ("operator_export", None),
    ("videos", None),
    ("template", None),
    ("cameras", ()),
])
def test_each_input_is_required_on_its_own(tmp_path, field, value):
    assert missing(complete(tmp_path, **{field: value})), f"{field} обязателен"


def test_the_refusal_speaks_the_customers_words(tmp_path):
    """«--group не задан» человеку с папкой работ не говорит ничего."""
    said = " ".join(missing(complete(tmp_path, group="")))
    assert "группа" in said.lower()
    assert "--" not in said, "имена ключей командной строки в отказе не нужны"


def test_a_file_that_does_not_exist_counts_as_missing(tmp_path):
    """Путь мимо файла — не «данные есть», а опечатка в пути."""
    assert missing(complete(tmp_path, operator_export=tmp_path / "нет.xlsx"))


def test_every_missing_input_is_named_at_once(tmp_path):
    """Отказ по одному — это шесть запусков подряд вместо одного разговора.

    Шесть: группа ОП, номер ОП, выгрузка оператора, видеозаписи, направления
    камер и шаблон заказчика.
    """
    bare = Inputs(group="", stop="", operator_export=None, videos=None,
                   template=None, cameras=())
    assert len(missing(bare)) == 6


def test_a_camera_without_a_direction_is_missing(tmp_path):
    """Направление задаётся на камеру: одно на две камеры — это догадка."""
    assert missing(complete(tmp_path, cameras=()))


# ---- Фамилия расшифровщика ----------------------------------------------------


def test_the_decoder_name_comes_from_the_setting():
    assert decoder_name("", {"DECODER_NAME": "Бучкурашвили Георгий"}) \
        == "Бучкурашвили Георгий"


def test_an_explicit_name_wins_over_the_setting():
    """Расшифровщик бывает другой — тогда его называют прямо."""
    assert decoder_name("Иванов Иван", {"DECODER_NAME": "Бучкурашвили Георгий"}) \
        == "Иванов Иван"


def test_a_name_from_nowhere_is_refused():
    """Пустая графа O не пройдёт приёмку, а выдуманная — хуже пустой."""
    with pytest.raises(ValueError, match="расшифровщик"):
        decoder_name("", {})


def test_a_blank_setting_is_the_same_as_none():
    with pytest.raises(ValueError, match="расшифровщик"):
        decoder_name("", {"DECODER_NAME": "   "})
