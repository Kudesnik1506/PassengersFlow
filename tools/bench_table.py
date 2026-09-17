"""Сводная таблица: эталон визита, разметка дверей и все методы рядом.

    uv run python tools/bench_table.py

Отвечает на вопрос «где метод ошибается», поэтому главная таблица — по
дверям: видно не «3 из 3», а какая именно дверь теряется и кем.

Источников четыре, и ни один не додумывается за другой: разметка дверей
(`data/truth/doors`), эталонные строки (`data/truth/rows.csv`), поправки часов
(`data/clocks`) и выгрузка стенда (`out/bench_doors.json`). Незаполненное
остаётся пустым — пустая клетка значит «не замерено», а не «ноль».

Наружу уходят только числа и графы бланка (решение 018): кадры не сохраняются.
"""

from __future__ import annotations

import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from rich.console import Console  # noqa: E402
from rich.table import Table  # noqa: E402

from paxcount.bench.cases import case_from_layout  # noqa: E402
from paxcount.bench.run import OK  # noqa: E402
from paxcount.bench.score import score_case  # noqa: E402
from paxcount.bench.summary import (  # noqa: E402
    camera_times, door_matches, load_runs, match_row,
)
from paxcount.cameras import COUNTING_CAMERAS  # noqa: E402
from paxcount.delivery import clocks  # noqa: E402
from paxcount.delivery.reconcile import comment_code  # noqa: E402
from paxcount.delivery.timeline import parse_slot, sessions_from_names  # noqa: E402
from paxcount.settings import DATA_DIR, OUT_DIR, videos_in  # noqa: E402
from paxcount.truth import load_door_layout, visit_moment  # noqa: E402
from paxcount import truth_rows  # noqa: E402

# Ширина задана, а не взята из терминала: таблица по дверям — это 7 граф плюс
# по графе на метод, и в узком окне rich сворачивает их в лапшу из одной буквы.
console = Console(width=190)
CAMERAS = ("1", "2", "3")
SHORT = {"edges": "edg", "ensemble": "ens", "groundingdino": "gdn", "motion": "mot",
          "openvocab": "ovc", "owlv2": "owl", "pixels": "pix", "vlm": "vlm",
          "vlpart": "vlp", "wheels": "whl"}
STOP = "22739"


def _slots():
    """Куски записи по именам файлов. Отладочные ролики названы не по-операторски
    и в шкалу смены не идут — их имя не разбирается, и это не ошибка."""
    names = []
    for path in videos_in(None):
        try:
            parse_slot(path.stem)
        except ValueError:
            continue
        names.append(path.stem)
    return [s for session in sessions_from_names(names) for s in session.slots]


def _stamp(time) -> str:
    return time.moment.strftime("%d.%m %H:%M:%S") if time.moment else ""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--doors", type=Path, default=DATA_DIR / "truth" / "doors")
    parser.add_argument("--rows", type=Path, default=DATA_DIR / "truth" / "rows.csv")
    parser.add_argument("--bench", type=Path, default=OUT_DIR / "bench_doors.json")
    parser.add_argument("--out", type=Path, default=OUT_DIR)
    args = parser.parse_args()

    records = clocks.load(DATA_DIR / "clocks" / f"{STOP}.csv")
    rows = truth_rows.load(args.rows)
    runs = load_runs(args.bench)
    slots = _slots()
    methods = sorted({r.method for r in runs})

    visits, doors = [], []
    for path in sorted(args.doors.rglob("*.json")):
        layout = load_door_layout(path)
        if layout.camera not in COUNTING_CAMERAS:
            continue
        case, problem = case_from_layout(layout)
        if case is None:
            console.print(f"[yellow]пропущено:[/yellow] {problem}")
            continue

        _CASES[case.visit_key] = case
        moment = visit_moment(layout.visit_key)
        reference = clocks.to_reference(moment, layout.camera, layout.video, records)
        times = {t.camera: t for t in camera_times(
            reference, CAMERAS, records, slots, marked_on=layout.camera)}
        row = match_row(layout.camera, moment, rows)
        code, note = comment_code(layout)
        matches = door_matches(case, runs)

        visits.append({
            "№": row.number if row else "",
            "К1": _stamp(times["1"]), "К2": _stamp(times["2"]),
            "К3": _stamp(times["3"]),
            "камера разметки": layout.camera,
            "тип ТС": (row.vehicle_kind if row else "") or "",
            "размер": layout.size.value,
            "маршрут": (row.route if row else "") or "",
            "борт": (row.board_number if row else "") or "",
            "госномер": (row.state_number if row else "") or "",
            "дверей всего": case.doors_total,
            "дверей в кадре": len(case.doors_visible),
            "код": "" if code is None else code,
            "вошло": "" if not row or row.boarded is None else row.boarded,
            "вышло": "" if not row or row.alighted is None else row.alighted,
            "видеофайл": layout.video,
            "кадр": layout.frame_idx,
        })
        if note:
            console.print(f"[yellow]визит {row.number if row else layout.visit_key}:"
                           f"[/yellow] {note}")

        visible = 0
        for door in layout.doors:
            found = matches[visible] if door.in_frame else {}
            width = (round(door.opening_px[2] - door.opening_px[0])
                      if door.opening_px else "")
            doors.append({
                "№": row.number if row else "", "борт": (row.board_number if row else "") or "",
                "дверь": door.n_from_nose,
                "в кадре": "да" if door.in_frame else "нет",
                "ширина проёма px": width,
                "вошло": "", "вышло": "",
                **{m: _cell(found.get(m), door.in_frame) for m in methods},
            })
            visible += 1 if door.in_frame else 0

    _print(visits, doors, methods, runs)
    args.out.mkdir(parents=True, exist_ok=True)
    _csv(args.out / "bench_table_visits.csv", visits)
    _csv(args.out / "bench_table_doors.csv", doors)
    console.print(f"\nтаблицы: {args.out / 'bench_table_visits.csv'}, "
                   f"{args.out / 'bench_table_doors.csv'}")
    return 0


def _cell(match, in_frame: bool) -> str:
    """Клетка «метод × дверь»: промах центра в пикселях, а не галочка.

    Галочка врала бы: «накрыл» при проёме в 5 px и запасе кропа в 140 px стоит
    и у метода, показавшего на соседнюю дверь. Число показывает, насколько
    попадание настоящее, одной и той же мерой для всех методов.
    """
    if not in_frame:
        return "—"          # дверь за краем кадра: метод не мог её найти
    if match is None:
        return "·"          # метод не отработал на этом визите
    return str(round(match.center_error_px)) if match.hit else "нет"


def _csv(path: Path, rows: list[dict]) -> None:
    if not rows:
        return
    with path.open("w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _print(visits, doors, methods, runs) -> None:
    table = Table(title="Визиты эталона", show_lines=False)
    for column in ("№", "К1", "К2", "К3", "тип ТС", "размер", "маршрут", "борт",
                    "госномер", "дверей", "в кадре", "код", "вошло", "вышло"):
        table.add_column(column, no_wrap=True)
    table.add_column("видеофайл", overflow="ellipsis", no_wrap=True, max_width=24)
    for v in visits:
        table.add_row(str(v["№"]), v["К1"], v["К2"], v["К3"], v["тип ТС"],
                       v["размер"], v["маршрут"], v["борт"], v["госномер"],
                       str(v["дверей всего"]), str(v["дверей в кадре"]),
                       str(v["код"]), str(v["вошло"]), str(v["вышло"]),
                       v["видеофайл"])
    console.print(table)
    console.print("[dim]время на камере разметки — замер, на остальных — пересчёт "
                   "по поправке часов; пустая клетка — не замерено[/dim]")

    detail = Table(title="Двери: промах центра в px («нет» — дверь не найдена, «—» — дверь за кадром)")
    for column in ("№", "борт", "дверь", "в кадре", "ширина", "вошло", "вышло"):
        detail.add_column(column, no_wrap=True)
    for method in methods:
        detail.add_column(SHORT.get(method, method[:3]), no_wrap=True)
    for d in doors:
        detail.add_row(str(d["№"]), d["борт"], str(d["дверь"]), d["в кадре"],
                        str(d["ширина проёма px"]), str(d["вошло"]), str(d["вышло"]),
                        *[d[m] for m in methods])
    console.print(detail)
    console.print("[dim]" + ",  ".join(f"{SHORT.get(m, m[:3])} — {m}" for m in methods)
                   + "[/dim]")

    # Лишние предсказания рядом с процентом найденных — иначе метод, засыпающий
    # кадр кандидатами, читается как лучший. Столбец «кандидатов на дверь»
    # показывает, сколько прямоугольников надо перебрать на одну найденную.
    summary = Table(title="Методы: найдено и чем заплачено")
    for column in ("метод", "состояние", "найдено", "лишних",
                    "кандидатов на дверь"):
        summary.add_column(column)
    for method in methods:
        mine = [r for r in runs if r.method == method]
        ok = [r for r in mine if r.status == OK]
        if not ok:
            summary.add_row(method, mine[0].status if mine else "", "", "", "")
            continue
        found = total = extras = 0
        for run in ok:
            case = _case_of(run.visit_key)
            if case is None:
                continue
            score = score_case(list(case.doors_visible), list(run.predicted))
            found += score.found; total += score.total; extras += score.extras
        summary.add_row(method, OK, f"{found}/{total}", str(extras),
                         f"{(found + extras) / found:.1f}" if found else "—")
    console.print(summary)


_CASES: dict = {}


def _case_of(visit_key: str):
    return _CASES.get(visit_key)


if __name__ == "__main__":
    raise SystemExit(main())
