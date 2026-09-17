"""Реестр сравниваемых методов: фабрики, а не готовые объекты.

Импорт реестра обязан быть дешёвым и безусловным. Положи сюда готовые
экземпляры — и `import methods` потянет за собой ultralytics, transformers и
detectron2 разом; проверить сам прогон стало бы нельзя без всех трёх, а строка
таблицы «не установлен» перестала бы существовать: вместо неё был бы обвал
импорта.

Отложенные подходы лежат отдельным списком с причиной. Их отсутствие в реестре
— решение, а не пропуск, и таблица результатов должна уметь это показать:
читающий обязан видеть, что метода нет потому, что его отложили, а не потому,
что он провалился.
"""

from __future__ import annotations

from collections.abc import Callable

from ..finders import DoorFinder


def _openvocab() -> DoorFinder:
    from .legacy import OpenVocabFinder

    return OpenVocabFinder()


def _pixels() -> DoorFinder:
    from .legacy import PixelFinder

    return PixelFinder()


def _edges() -> DoorFinder:
    from .classic import EdgeFinder

    return EdgeFinder()


def _motion() -> DoorFinder:
    from .classic import MotionFinder

    return MotionFinder()


def _wheels() -> DoorFinder:
    from .classic import WheelFinder

    return WheelFinder()


def _owlv2() -> DoorFinder:
    from .vision import OwlV2Finder

    return OwlV2Finder()


def _groundingdino() -> DoorFinder:
    from .vision import GroundingDinoFinder

    return GroundingDinoFinder()


def _vlpart() -> DoorFinder:
    from .vlpart import VLPartFinder

    return VLPartFinder()


def _vlm() -> DoorFinder:
    from .manual import VlmFinder

    return VlmFinder()


def _ensemble() -> DoorFinder:
    from .ensemble import EnsembleFinder

    return EnsembleFinder()


REGISTRY: dict[str, Callable[[], DoorFinder]] = {
    "openvocab": _openvocab,
    "pixels": _pixels,
    "edges": _edges,
    "motion": _motion,
    "wheels": _wheels,
    "owlv2": _owlv2,
    "groundingdino": _groundingdino,
    "vlpart": _vlpart,
    "vlm": _vlm,
    "ensemble": _ensemble,
}

DESCRIPTIONS: dict[str, str] = {
    "openvocab": "наше прежнее: YOLOWorld с запечёнными классами дверей",
    "pixels": "наше прежнее: непохожесть на цвет борта в Lab",
    "edges": "промежуток между двумя сильными вертикальными краями",
    "motion": "временная разность: дверь — единственное, что меняется",
    "wheels": "колёса задают метр, метр отсеивает неправдоподобную ширину",
    "owlv2": "OWLv2 по образцу двери с другого визита",
    "groundingdino": "GroundingDINO по текстовому описанию двери",
    "vlpart": "VLPart: у него «дверь автобуса» — отдельный класс",
    "vlm": "указание мультимодальной моделью, ответ получен в сессии",
    "ensemble": "согласие методов, число дверей из таблицы 3 — арбитр",
}

DEFERRED: dict[str, str] = {
    "people": (
        "двери по следам людей: метод работает на статистике за сотни визитов, "
        "на пяти машинах сгущения не образуются в принципе. Отложен владельцем "
        "до прогона по всей папке «Утро» трёх камер"
    ),
    "finetune": (
        "дообучение: обучать не на чем, пока размечено пять визитов. Нужны "
        "150-300 кадров разнообразия, то есть 30-50 визитов. Отложено владельцем"
    ),
}
