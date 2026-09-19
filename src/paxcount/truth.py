"""Разметка дверей эталонного визита и проверка её полноты.

Разметка — на ВИЗИТ, не на видео: одна и та же камера снимает машину, вставшую
перед контрольной точкой, и машину, вставшую за ней, и общего дверного проёма
у них может не быть вовсе (ближняя дверь одной вне кадра другой — законна).

Арифметика ловит ровно ту ошибку, которая стоила часа на борту 7861: три двери
по размеру (таблица 3 инструкции), но в разметке — только две, потому что
границы кропа выставлялись на глаз после взгляда на кадр, а не по файлу,
составленному ДО счёта (метод 1-2 плана). Число дверей по размеру обязано
совпасть с суммой «в кадре» и «за краем» — иначе разметка неполна, и это видно
раньше, чем начнётся счёт людей.
"""

from __future__ import annotations

import json
from pathlib import Path

from pydantic import BaseModel, model_validator

from .delivery.model import DOORS_BY_SIZE, VehicleSize
# Импорт, а не копия (запрет 8): тот же порог решает тот же вопрос в
# автопредложении — можно ли объявить дверь за кадром (решение 038).
from .settings import EDGE_TOUCH_PX


class DoorLayoutEntry(BaseModel):
    """Одна дверь визита: номер от носа, пиксели проёма, видимость."""

    n_from_nose: int
    opening_px: tuple[float, float, float, float] | None = None
    in_frame: bool

    @model_validator(mode="after")
    def _pixels_match_visibility(self) -> "DoorLayoutEntry":
        if self.in_frame and self.opening_px is None:
            raise ValueError(
                f"дверь {self.n_from_nose}: помечена в кадре, но координаты проёма не заданы"
            )
        if not self.in_frame and self.opening_px is not None:
            raise ValueError(
                f"дверь {self.n_from_nose}: помечена вне кадра, но координаты заданы — "
                "если проём виден, ставьте in_frame=True"
            )
        return self


class DoorLayout(BaseModel):
    """Разметка всех дверей одного визита на одной камере.

    Кадр назван обязательно (`video` + `frame_idx` + `frame_size`): пиксели без
    кадра нельзя ни проверить, ни нарезать по ним кроп — они годятся ровно до
    конца сессии, в которой нарисованы, а эталон живёт дольше.

    `body_px` — рамка кузова на том же кадре, размеченная человеком. Она не
    украшение: именно по ней видно, обрезан корпус краем кадра или виден
    целиком, а от этого зависит, законна ли галочка «дверь за кадром».
    """

    visit_key: str
    camera: str
    size: VehicleSize
    orientation: str
    video: str
    frame_idx: int
    frame_size: tuple[int, int]
    body_px: tuple[float, float, float, float] | None = None
    doors: list[DoorLayoutEntry]

    @model_validator(mode="after")
    def _body_box_is_sane(self) -> "DoorLayout":
        if self.body_px is None:
            return self
        x0, y0, x1, y1 = self.body_px
        if x1 <= x0 or y1 <= y0:
            raise ValueError(
                f"рамка кузова вывернута: {self.body_px} — это опечатка "
                "разметчика, а не узкий кузов"
            )
        return self


def check_door_arithmetic(layout: DoorLayout) -> list[str]:
    """Замечания по полноте разметки. Пустой список — разметку можно считать.

    Три проверки, каждая — от конкретной ошибки этой сессии:
    1. число дверей = в кадре + за краем, по размеру из таблицы 3 (7861);
    2. номера от носа не повторяются (две зоны, принятые за одну дверь);
    3. номера от носа идут подряд без пропуска (дверь, забытая при разметке,
       а не только при счёте).
    """
    problems: list[str] = []
    expected = DOORS_BY_SIZE.get(layout.size)
    if expected is not None and len(layout.doors) != expected:
        problems.append(
            f"{layout.size.value}: по таблице 3 дверей {expected}, в разметке "
            f"{len(layout.doors)} (в кадре {sum(d.in_frame for d in layout.doors)}, "
            f"за краем {sum(not d.in_frame for d in layout.doors)})"
        )

    numbers = [d.n_from_nose for d in layout.doors]
    if len(set(numbers)) != len(numbers):
        problems.append(f"повтор номера двери: {sorted(numbers)}")
    elif numbers and sorted(numbers) != list(range(min(numbers), max(numbers) + 1)):
        problems.append(f"разрыв в нумерации дверей от носа: {sorted(numbers)}")

    problems.extend(_body_problems(layout))
    return problems


def _body_problems(layout: DoorLayout) -> list[str]:
    """Проверки, опирающиеся на рамку кузова. Без рамки молчат.

    Молчание здесь — не поблажка: судить об обрезке корпуса без его рамки
    нечем, а замечание, выведенное из ничего, хуже отсутствующего.
    """
    if layout.body_px is None:
        return []

    problems: list[str] = []
    x0, y0, x1, y1 = layout.body_px
    width, height = layout.frame_size
    truncated = x0 <= EDGE_TOUCH_PX or x1 >= width - EDGE_TOUCH_PX

    hidden = [d.n_from_nose for d in layout.doors if not d.in_frame]
    if hidden and not truncated:
        # Числа, а не приговор. Порог «упёрся в край» (8 px) взят для рамок
        # детектора, который точен; рукой на вписанном кадре, где экранный
        # пиксель равен двум кадровым, в него не попасть — и замечание
        # «кузов виден целиком» при зазоре в 29 px оказалось неправдой
        # (боевая разметка борта 7326, К2, кадр 1530). Пусть расстояния
        # называются, а судит человек.
        problems.append(
            f"двери {hidden} помечены за кадром, но рамка кузова не доходит до "
            f"краёв кадра: слева {x0:.0f} px, справа {width - x1:.0f} px. "
            "Если кузов обрезан краем — протяните рамку за границу кадра, она "
            "обрежется ровно по ней; если нет — дверь в кадре есть, и её надо "
            "найти, а не объявить невидимой"
        )
    elif hidden:
        problems.extend(_side_problems(layout, hidden, x0 <= EDGE_TOUCH_PX,
                                        x1 >= width - EDGE_TOUCH_PX))

    outside = [
        d.n_from_nose for d in layout.doors
        if d.opening_px is not None and not _inside(d.opening_px, layout.body_px)
    ]
    if outside:
        problems.append(
            f"проёмы дверей {outside} лежат вне рамки кузова {layout.body_px} — "
            "размечена соседняя машина или промах мыши"
        )
    return problems


def _side_problems(layout: DoorLayout, hidden: list[int],
                    cut_left: bool, cut_right: bool) -> list[str]:
    """Дверь за кадром обязана быть с той стороны, где кадр и обрезан.

    Обрезка кузова сама по себе ещё ничего не оправдывает: у кузова, упёршегося
    в ПРАВЫЙ край, первая от носа дверь (при носе слева — левая) в кадре есть,
    и галочка «не видно» прячет не съёмку, а недоработку разметчика. Найдено на
    первом же файле разметки эталона.
    """
    visible = [d.n_from_nose for d in layout.doors if d.in_frame]
    if not visible:
        return []

    nose_left = layout.orientation != "нос-справа"
    problems: list[str] = []
    for n in hidden:
        if min(visible) < n < max(visible):
            problems.append(
                f"дверь {n} помечена за кадром, но между видимыми дверями "
                f"{min(visible)} и {max(visible)}: за краем кадра она быть не может"
            )
            continue
        # Номер меньше всех видимых — дверь к носу; больше — к корме.
        toward_nose = n < min(visible)
        on_left = toward_nose if nose_left else not toward_nose
        if on_left and not cut_left:
            problems.append(
                f"дверь {n} помечена за левым краем, но кузов обрезан не с той "
                f"стороны ({layout.orientation}, обрезан "
                f"{'правый' if cut_right else 'ни один'} край)"
            )
        elif not on_left and not cut_right:
            problems.append(
                f"дверь {n} помечена за правым краем, но кузов обрезан не с той "
                f"стороны ({layout.orientation}, обрезан "
                f"{'левый' if cut_left else 'ни один'} край)"
            )
    return problems


def _inside(box: tuple[float, float, float, float],
             body: tuple[float, float, float, float]) -> bool:
    """Центр проёма внутри кузова. Именно центр, а не рамка целиком.

    Запас кропа и наклон кузова законно выводят края проёма за рамку ТС на
    десятки пикселей; центр за её пределами — это уже другая машина.
    """
    cx = (box[0] + box[2]) / 2
    cy = (box[1] + box[3]) / 2
    return body[0] <= cx <= body[2] and body[1] <= cy <= body[3]


# Перекрытие рамок кузова, начиная с которого две записи считаются одним
# визитом. Оба края порога замерены на боевой разметке, а не выбраны на глаз:
# борт 7326, перерисованный после правки, дал 0.98; две разные машины той же
# камеры — 0.41 (7326 против 38142) и 0.08 (38142 против машины 06:59:34).
SAME_VISIT_IOU = 0.8

# Наибольший разрыв во времени, при котором две записи ещё могут быть одним
# визитом. Одной геометрии мало: борта 38142 и 38157 вставали на одно место, и
# их рамки перекрываются выше порога — но между ними пять минут. Замеры:
# две записи одного визита отстояли на 1 и 2 секунды, ближайшие РАЗНЫЕ визиты
# той же камеры — на 43 секунды, а сама стоянка длится 11-20 секунд.
SAME_VISIT_GAP_S = 20.0


def visit_moment(visit_key: str):
    """Время визита из его ключа. `None` — ключ не по соглашению, судим по месту."""
    from datetime import datetime

    parts = visit_key.split("/")
    if len(parts) < 2:
        return None
    try:
        return datetime.fromisoformat(parts[1])
    except ValueError:
        return None


def iou(a: tuple[float, float, float, float],
          b: tuple[float, float, float, float]) -> float:
    inter_w = max(0.0, min(a[2], b[2]) - max(a[0], b[0]))
    inter_h = max(0.0, min(a[3], b[3]) - max(a[1], b[1]))
    inter = inter_w * inter_h
    union = (a[2] - a[0]) * (a[3] - a[1]) + (b[2] - b[0]) * (b[3] - b[1]) - inter
    return inter / union if union > 0 else 0.0


def duplicate_visit_problems(
    layout: DoorLayout, existing: list[DoorLayout]
) -> list[str]:
    """Не записан ли этот визит уже — под другим ключом.

    Ключ визита строится из точного времени кадра, поэтому шаг на секунду и
    повторное сохранение дают не правку записи, а вторую запись той же машины.
    Замерено дважды: борт 38142 записан кадрами 4926 и 4956 с разметкой,
    совпавшей до пикселя; борт 7326 — кадрами 1530 и 1590, где рамка кузова
    ещё и перерисовывалась (исправляли замечание). В стенде такая машина идёт
    дважды, а в эталоне вместо шести визитов значится семь.

    Признак — перекрытие рамок кузова, а не совпадение до пикселя и не сам
    факт пересечения. Совпадение до пикселя пропустило второй случай:
    исправленная рамка отличается от прежней. Пересечение же оказалось
    бесполезным в другую сторону — борт 7326 занимает кадр от 29-го до
    1744-го пикселя из 1920, и внутрь его рамки попадает центр любой
    следующей машины.
    """
    if layout.body_px is None:
        return []

    mine = visit_moment(layout.visit_key)
    problems: list[str] = []
    for other in existing:
        if other.visit_key == layout.visit_key:
            continue  # тот же ключ — это правка записи, а не дубль
        if other.camera != layout.camera or other.video != layout.video:
            continue  # разные камеры видят разные позиции стоянки
        if other.body_px is None:
            continue
        theirs = visit_moment(other.visit_key)
        if mine is not None and theirs is not None:
            if abs((mine - theirs).total_seconds()) > SAME_VISIT_GAP_S:
                continue  # столько машина не стоит — это другой визит
        overlap = iou(layout.body_px, other.body_px)
        if overlap < SAME_VISIT_IOU:
            continue
        when = other.visit_key.split("/")[1] if "/" in other.visit_key else other.visit_key
        problems.append(
            f"на том же месте уже записан визит {when} (кадр {other.frame_idx}), "
            f"рамки кузова совпадают на {overlap:.0%}. Если это та же машина — "
            "не сохраняйте вторую запись, а откройте её из списка «Размечено» "
            "и поправьте; иначе в эталоне окажется два визита вместо одного"
        )
    return problems


def door_layout_path(camera: str, visit_key: str, root: Path) -> Path:
    """Файл разметки визита: `<root>/<камера>/<ключ визита>.json`.

    Ключ визита содержит и косую черту, и двоеточие (`2/07:02:06/1596`) — в
    имени файла они заменяются, но не выбрасываются: два разных визита одной
    камеры обязаны дать два разных файла, иначе разметка второго молча затрёт
    первую.
    """
    slug = visit_key.replace("/", "_").replace(":", "-").strip("_")
    return root / camera / f"{slug}.json"


def save_door_layout(path: Path, layout: DoorLayout) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(layout.model_dump_json(indent=2) + "\n", encoding="utf-8")


def load_door_layout(path: Path) -> DoorLayout:
    return DoorLayout.model_validate(json.loads(path.read_text(encoding="utf-8")))
