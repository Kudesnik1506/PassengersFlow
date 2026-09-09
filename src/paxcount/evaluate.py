"""Метрики против ручного эталона data/truth/truth.csv."""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

TARGET_ERROR = 0.10  # порог приёмки из плана: суммарная погрешность ≤ 10 %

# Отраслевая практика (VDV 457, по которому проходят приёмку APC-системы
# европейского транспорта) устроена иначе, чем одна суммарная погрешность, и
# в двух местах это прямо про наши данные:
#
# 1. Главный критерий там — систематическое смещение, а не модуль ошибки.
#    Разнонаправленные ошибки на сотне остановок гасят друг друга, смещение
#    накапливается. У нас смещение −40 %: мы всегда недосчитываем, потому что
#    настраивали систему молчать при неуверенности.
# 2. Допуск задаётся в пассажирах, а не в процентах: доля остановок, где
#    ошиблись не больше чем на одного и на двух — «включая остановки, где
#    посадки и высадки не было». Процент от нулевого эталона неисчислим, а
#    таких роликов у нас четыре из шести размеченных, и они самые ценные:
#    проверяют, что система не срабатывает на пустом месте.
#
# Пороги ниже — ориентир из стандарта, а не наша приёмка: они установлены для
# датчиков в дверном проёме, а не для наружной камеры. Служат шкалой, чтобы
# видеть расстояние до отраслевого уровня.
VDV_EXACT_SHARE = 0.85  # доля дверных циклов, посчитанных точно
VDV_WITHIN_1_SHARE = 0.90  # доля остановок с ошибкой не больше ±1 пассажира
VDV_WITHIN_2_SHARE = 0.97  # то же для ±2


@dataclass(frozen=True)
class CountQuality:
    """Качество счёта по набору единиц измерения.

    Единица — пара «видео + направление»: эталон размечен по видео, поэтому
    ближе к «дверному циклу» из стандарта мы пока не подбираемся. Когда эталон
    станет повизитным, единицей станет визит — сами метрики не изменятся.
    """

    units: int
    exact: float  # доля посчитанных точно
    within_1: float  # доля с ошибкой не больше ±1 пассажира
    within_2: float  # то же для ±2
    bias: float  # относительное смещение со знаком: <0 — недосчёт
    error: float  # суммарная погрешность: сумма модулей ошибок к сумме эталона
    mae: float  # средняя абсолютная ошибка на единицу


def count_quality(pairs: Sequence[tuple[float, float]]) -> CountQuality:
    """Метрики по парам (насчитано, эталон).

    Функция намеренно не знает ни про pandas, ни про формат прогона: единственный
    её вход — числа. Так она проверяется тестами без файлов на диске и
    переиспользуется всюду, где такие пары есть.
    """
    if not pairs:
        nan = float("nan")
        return CountQuality(0, nan, nan, nan, nan, nan, nan)

    errors = [abs(counted - truth) for counted, truth in pairs]
    total_true = sum(truth for _, truth in pairs)
    total_pred = sum(counted for counted, _ in pairs)
    units = len(pairs)

    # Смещение и погрешность считаются от суммы эталона. Если событий не было
    # вовсе, обе величины не определены — и выдумывать им значение нельзя:
    # «ноль ошибок из нуля событий» не означает ни хорошего, ни плохого счёта.
    # Доли при этом остаются осмысленными, поэтому считаются всегда.
    relative = float("nan") if total_true == 0 else None

    return CountQuality(
        units=units,
        exact=sum(e == 0 for e in errors) / units,
        within_1=sum(e <= 1 for e in errors) / units,
        within_2=sum(e <= 2 for e in errors) / units,
        bias=relative if relative is not None else (total_pred - total_true) / total_true,
        error=relative if relative is not None else sum(errors) / total_true,
        mae=sum(errors) / units,
    )


# Целевой сценарий — статичная камера на штативе, ТС целиком в кадре (см.
# «Область применимости» в data/videos/SOURCES.md). Часть набора ему не
# отвечает: метро Гаосюна 640×480 взято ради плотной толпы, съёмка с рук —
# заведомо негативный случай. Смешивать их в одну цифру значит оценивать
# систему по материалу, для которого она не предназначена, и одновременно
# прятать провалы на целевых сценах за чужими роликами.
TARGET_DOMAIN = "целевой"
DEBUG_DOMAIN = "отладочный"
KNOWN_DOMAINS = frozenset({TARGET_DOMAIN, DEBUG_DOMAIN})
WHOLE_SET = "весь набор"


def unknown_domains(values: Iterable[str]) -> set[str]:
    """Значения домена, которых быть не должно.

    Опечатка обязана падать, а не заводить третий домен молча: «целевои»
    вместо «целевой» дал бы лишнюю строку в отчёте, а целевой домен незаметно
    похудел бы на это видео. Ровно так уже терялись ошибки разметки дверей.
    """
    return {str(v) for v in values} - set(KNOWN_DOMAINS)


def quality_by_domain(
    rows: Sequence[tuple[str, float, float]],
) -> dict[str, CountQuality]:
    """Метрики по каждому домену плюс общая цифра.

    Вход — тройки (домен, насчитано, эталон). Общая цифра остаётся не для
    приёмки, а для сопоставимости с прошлыми замерами: по ней принято решений
    больше, чем стоило, и обрывать ряд посреди работы значит потерять историю.
    """
    if not rows:
        return {}
    by_domain: dict[str, list[tuple[float, float]]] = {}
    for domain, counted, truth in rows:
        by_domain.setdefault(domain, []).append((counted, truth))

    result = {d: count_quality(pairs) for d, pairs in sorted(by_domain.items())}
    result[WHOLE_SET] = count_quality([(c, t) for _, c, t in rows])
    return result


def load_truth(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"нет эталона: {path}. Создайте его командой paxcount truth-template"
        )
    df = pd.read_csv(path)
    totals = df.groupby("video", as_index=False)[["boarded", "alighted"]].sum()
    # Домен — свойство ролика, а не визита: у видео с несколькими визитами он
    # один и тот же, поэтому берётся первое значение, а не сумма.
    if "domain" in df.columns:
        first = df.groupby("video", as_index=False)["domain"].first()
        totals = totals.merge(first, on="video", how="left")
    return totals


def collect_runs(run_dir: Path) -> pd.DataFrame:
    rows = []
    for visits in sorted(run_dir.rglob("visits.csv")):
        df = pd.read_csv(visits)
        if df.empty:
            continue
        rows.append(
            {
                "backend": visits.parent.parent.name,
                "video": df["video"].iloc[0],
                "boarded": int(df["boarded"].sum()),
                "alighted": int(df["alighted"].sum()),
                "visits": len(df),
            }
        )
    return pd.DataFrame(rows)


def compare_runs(run_dir: Path, truth_path: Path, console: Console) -> pd.DataFrame:
    truth = load_truth(truth_path)
    runs = collect_runs(run_dir)
    if runs.empty:
        console.print("[red]в папке прогона нет visits.csv[/red]")
        return runs

    merged = runs.merge(truth, on="video", suffixes=("", "_true"), how="left")
    merged["err_in"] = (merged["boarded"] - merged["boarded_true"]).abs()
    merged["err_out"] = (merged["alighted"] - merged["alighted_true"]).abs()

    per_video = Table(title="По видео")
    for col in ("бэкенд", "видео", "вошло", "эталон", "вышло", "эталон", "ошибка"):
        per_video.add_column(col)
    for _, r in merged.iterrows():
        err = r["err_in"] + r["err_out"]
        per_video.add_row(
            r["backend"], r["video"], str(r["boarded"]),
            _fmt(r.get("boarded_true")), str(r["alighted"]),
            _fmt(r.get("alighted_true")),
            "—" if pd.isna(r.get("boarded_true")) else str(int(err)),
        )
    console.print(per_video)

    summary = Table(title=f"Итог по бэкендам (порог приёмки {TARGET_ERROR:.0%})")
    for col in ("бэкенд", "эталон", "насчитано", "ошибок", "погрешность",
                "смещение", "MAE", "вердикт"):
        summary.add_column(col)
    quality: dict[str, CountQuality] = {}
    for backend, g in merged.dropna(subset=["boarded_true"]).groupby("backend"):
        # Единица измерения — пара «видео + направление»: сумма модулей ошибок,
        # а не разность сумм, иначе перелёт по входу гасит недолёт по выходу и
        # метрика показывает несуществующую точность.
        pairs = [(r["boarded"], r["boarded_true"]) for _, r in g.iterrows()]
        pairs += [(r["alighted"], r["alighted_true"]) for _, r in g.iterrows()]
        q = count_quality(pairs)
        quality[backend] = q

        total_true = g["boarded_true"].sum() + g["alighted_true"].sum()
        total_pred = g["boarded"].sum() + g["alighted"].sum()
        errors = g["err_in"].sum() + g["err_out"].sum()
        summary.add_row(
            backend, str(int(total_true)), str(int(total_pred)), str(int(errors)),
            f"{q.error:.0%}", f"{q.bias:+.0%}", f"{q.mae:.2f}",
            "[green]проходит[/green]" if q.error <= TARGET_ERROR
            else "[red]не проходит[/red]",
        )
    console.print(summary)
    console.print(
        "[dim]погрешность — сумма модулей ошибок к сумме эталона; "
        "смещение показывает, в какую сторону систематически ошибается бэкенд[/dim]"
    )

    _print_tolerance(quality, console)
    _print_domains(merged, console)
    return merged


def _print_domains(merged: pd.DataFrame, console: Console) -> None:
    """Разделение по домену: где мы правда работаем, а где меряем себя чужим.

    Общая цифра усредняет целевые сцены с отладочными и потому не годится ни
    для одного решения: она хуже, чем есть на целевом домене, и лучше, чем на
    трудном. Разделение показывает обе, не пряча ни одну.
    """
    if "domain" not in merged.columns:
        return
    known = merged.dropna(subset=["boarded_true", "domain"])
    if known.empty:
        return

    table = Table(title="По домену съёмки")
    for col in ("бэкенд", "домен", "видео", "единиц", "погрешность", "смещение",
                "точно", "±1"):
        table.add_column(col)

    for backend, g in known.groupby("backend"):
        rows = [(r["domain"], r["boarded"], r["boarded_true"]) for _, r in g.iterrows()]
        rows += [(r["domain"], r["alighted"], r["alighted_true"]) for _, r in g.iterrows()]
        videos = g.groupby("domain")["video"].nunique().to_dict()
        for domain, q in quality_by_domain(rows).items():
            table.add_row(
                backend, domain, str(videos.get(domain, g["video"].nunique())),
                str(q.units), _fmt_ratio(q.error), f"{q.bias:+.0%}",
                f"{q.exact:.0%}", f"{q.within_1:.0%}",
            )
    console.print(table)
    console.print(
        f"[dim]целевой домен — статичная камера на штативе, ТС целиком в кадре "
        f"(см. «Область применимости» в data/videos/SOURCES.md); "
        f"«{WHOLE_SET}» оставлен для сопоставимости с прошлыми замерами[/dim]"
    )


def _print_tolerance(quality: dict[str, CountQuality], console: Console) -> None:
    """Допуск в пассажирах — то, что процентная метрика не измеряет.

    Проценты неисчислимы на роликах с нулевым эталоном, а их у нас четыре из
    шести размеченных. Здесь они полноценно участвуют: «ничего не произошло, и
    мы ничего не насчитали» — проверяемый результат, а не пропуск.
    """
    if not quality:
        return
    table = Table(title="Допуск в пассажирах (шкала VDV 457, не наша приёмка)")
    for col in ("бэкенд", "единиц", "точно", "±1", "±2"):
        table.add_column(col)

    def mark(value: float, target: float) -> str:
        colour = "green" if value >= target else "yellow"
        return f"[{colour}]{value:.0%}[/{colour}] / {target:.0%}"

    for backend, q in quality.items():
        table.add_row(
            backend, str(q.units),
            mark(q.exact, VDV_EXACT_SHARE),
            mark(q.within_1, VDV_WITHIN_1_SHARE),
            mark(q.within_2, VDV_WITHIN_2_SHARE),
        )
    console.print(table)
    console.print(
        "[dim]единица — пара «видео + направление»; через дробь ориентир VDV 457, "
        "установленный для датчиков в дверном проёме, а не для наружной камеры: "
        "это шкала расстояния до отраслевого уровня, а не порог приёмки[/dim]"
    )


def _fmt(value) -> str:
    return "—" if value is None or pd.isna(value) else str(int(value))


# ---- Оценка по событиям: precision/recall/F1, а не только сумма по визиту ----
#
# Агрегатный эталон (truth.csv) не различает «одно ложное плюс один пропуск»
# и «ничего не произошло» — оба дают нулевую ошибку по сумме. Здесь событие
# предсказания засчитывается только если рядом по времени есть эталонное
# событие того же направления: перелёт и недолёт больше не гасят друг друга.
MATCH_WINDOW_SECONDS = 1.5


def load_truth_events(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"нет эталона по событиям: {path}")
    df = pd.read_csv(path)
    df["count"] = df["count"].fillna(1).astype(int)
    return df


def collect_pred_events(run_dir: Path) -> pd.DataFrame:
    rows = []
    for events_path in sorted(run_dir.rglob("events.csv")):
        df = pd.read_csv(events_path)
        backend = events_path.parent.parent.name
        for _, r in df.iterrows():
            rows.append(
                {"backend": backend, "video": r["video"],
                 "ts": float(r["event_ts"]), "direction": r["direction"]}
            )
    return pd.DataFrame(rows, columns=["backend", "video", "ts", "direction"])


def _match_timed(
    truth_rows: pd.DataFrame, pred_ts: list[float], window: float
) -> tuple[int, list[float]]:
    """Жадное сопоставление эталонных меток времени с предсказанными.

    Возвращает число совпавших и список оставшихся (несопоставленных)
    предсказаний — они пойдут либо в счёт агрегатных строк, либо в FP.
    """
    remaining = list(pred_ts)
    matched = 0
    for ts in truth_rows["ts"]:
        best = None
        best_dist = window
        for p in remaining:
            d = abs(p - ts)
            if d <= best_dist:
                best, best_dist = p, d
        if best is not None:
            remaining.remove(best)
            matched += 1
    return matched, remaining


def compare_events(
    run_dir: Path, truth_path: Path, console: Console,
    window: float = MATCH_WINDOW_SECONDS,
) -> pd.DataFrame:
    """Precision/recall/F1 по событиям, раздельно для наблюдаемых и всех.

    Сопоставление по времени (окно ``window``) отличает «правильная сумма по
    неправильным причинам» от настоящего совпадения — то, чего агрегатная
    погрешность в ``compare_runs`` в принципе не видит.
    """
    truth = load_truth_events(truth_path)
    preds = collect_pred_events(run_dir)
    if preds.empty:
        console.print("[red]в папке прогона нет events.csv[/red]")
        return preds

    rows = []
    for backend in sorted(preds["backend"].unique()):
        for observable_only in (False, True):
            tp = fp = fn = 0
            # Обход шёл по парам (видео, направление) ИЗ ЭТАЛОНА, поэтому
            # предсказания того направления, которого в эталоне нет, не
            # проверялись вовсе и не попадали в FP. Замер, на котором это
            # вскрылось: конфигурация с одной рамкой на визит выдумывала на 04
            # четыре входа при эталоне «входов нет» — агрегатная погрешность
            # росла с 40% до 67%, а precision по событиям оставался 100%.
            # Поэтому список пар берётся из объединения эталона и предсказаний.
            # Только по видео, у которых эталон вообще есть: на роликах без
            # разметки эталона мы не знаем, верное событие или ложное, и
            # записывать их в FP значило бы штрафовать за неизвестное.
            pairs = set(map(tuple, truth[["video", "direction"]].drop_duplicates().to_numpy()))
            known = set(truth["video"].unique())
            mine = preds[(preds["backend"] == backend) & (preds["video"].isin(known))]
            pairs |= set(map(tuple, mine[["video", "direction"]].drop_duplicates().to_numpy()))
            for video, direction in sorted(pairs):
                g = truth[(truth["video"] == video) & (truth["direction"] == direction)]
                g = g[g["observable"] == "yes"] if observable_only else g
                timed = g[g["ts"].notna()]
                untimed_count = int(g.loc[g["ts"].isna(), "count"].sum())

                pred_mask = (
                    (preds["backend"] == backend)
                    & (preds["video"] == video)
                    & (preds["direction"] == direction)
                )
                pred_ts = preds.loc[pred_mask, "ts"].tolist()

                matched, leftover = _match_timed(timed, pred_ts, window)
                tp += matched
                fn += len(timed) - matched

                # Остаток предсказаний «гасит» агрегатные (нетаймированные)
                # строки эталона до их количества — точных меток там нет,
                # поэтому сопоставляем только по числу, не по времени.
                absorbed = min(len(leftover), untimed_count)
                tp += absorbed
                fn += untimed_count - absorbed
                fp += len(leftover) - absorbed

            precision = tp / (tp + fp) if (tp + fp) else float("nan")
            recall = tp / (tp + fn) if (tp + fn) else float("nan")
            f1 = (
                2 * precision * recall / (precision + recall)
                if (tp + fp) and (tp + fn) and (precision + recall) > 0
                else float("nan")
            )
            rows.append(
                {"backend": backend, "набор": "только наблюдаемые" if observable_only else "все",
                 "TP": tp, "FP": fp, "FN": fn, "precision": precision,
                 "recall": recall, "f1": f1}
            )

    table = Table(title=f"Точность/полнота по событиям (окно ±{window} с)")
    for col in ("бэкенд", "набор", "TP", "FP", "FN", "precision", "recall", "F1"):
        table.add_column(col)
    for r in rows:
        table.add_row(
            r["backend"], r["набор"], str(r["TP"]), str(r["FP"]), str(r["FN"]),
            _fmt_ratio(r["precision"]), _fmt_ratio(r["recall"]), _fmt_ratio(r["f1"]),
        )
    console.print(table)
    console.print(
        "[dim]TP — совпало по времени и направлению (или засчитано в счёт "
        "агрегатной строки эталона без точных меток); "
        "«только наблюдаемые» исключает события, которых с этой камеры "
        "физически не видно (observable=no) — не путать с полнотой в принципе[/dim]"
    )
    return pd.DataFrame(rows)


def _fmt_ratio(value: float) -> str:
    return "—" if pd.isna(value) else f"{value:.0%}"
