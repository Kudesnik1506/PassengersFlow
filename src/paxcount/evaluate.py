"""Метрики против ручного эталона data/truth/truth.csv."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
from rich.console import Console
from rich.table import Table

TARGET_ERROR = 0.10  # порог приёмки из плана: суммарная погрешность ≤ 10 %


def load_truth(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(
            f"нет эталона: {path}. Создайте его командой paxcount truth-template"
        )
    df = pd.read_csv(path)
    return df.groupby("video", as_index=False)[["boarded", "alighted"]].sum()


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
    for backend, g in merged.dropna(subset=["boarded_true"]).groupby("backend"):
        total_true = g["boarded_true"].sum() + g["alighted_true"].sum()
        total_pred = g["boarded"].sum() + g["alighted"].sum()
        # Сумма модулей ошибок, а не разность сумм: иначе перелёт по входу
        # гасит недолёт по выходу и метрика показывает несуществующую точность.
        errors = g["err_in"].sum() + g["err_out"].sum()
        mae = errors / max(len(g) * 2, 1)
        rate = errors / total_true if total_true else float("nan")
        bias = (total_pred - total_true) / total_true if total_true else float("nan")
        summary.add_row(
            backend, str(int(total_true)), str(int(total_pred)), str(int(errors)),
            f"{rate:.0%}", f"{bias:+.0%}", f"{mae:.2f}",
            "[green]проходит[/green]" if rate <= TARGET_ERROR else "[red]не проходит[/red]",
        )
    console.print(summary)
    console.print(
        "[dim]погрешность — сумма модулей ошибок к сумме эталона; "
        "смещение показывает, в какую сторону систематически ошибается бэкенд[/dim]"
    )
    return merged


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
