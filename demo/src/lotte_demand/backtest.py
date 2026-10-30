"""Rolling-origin backtest and accuracy metrics."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def wape(y: np.ndarray, yhat: np.ndarray) -> float:
    denom = np.abs(y).sum()
    return float(np.abs(y - yhat).sum() / denom) if denom > 0 else float("nan")


def bias(y: np.ndarray, yhat: np.ndarray) -> float:
    denom = np.abs(y).sum()
    return float((yhat - y).sum() / denom) if denom > 0 else float("nan")


def mase_scale(panel_long: pd.DataFrame, season: int = 12) -> pd.Series:
    """In-sample seasonal-naive MAE per series (scale for MASE)."""
    out = {}
    for uid, g in panel_long.groupby("unique_id"):
        y = g.sort_values("ds")["y"].to_numpy()
        out[uid] = float(np.mean(np.abs(y[season:] - y[:-season]))) if len(y) > season else np.nan
    return pd.Series(out, name="scale")


def rolling_backtest(models: dict[str, Any], panel_long: pd.DataFrame, exog: pd.DataFrame,
                     origins: list[pd.Timestamp], horizons: list[int], verbose: bool = True) -> pd.DataFrame:
    """Returns long frame: unique_id, origin, h, ds, model, yhat, y (actual)."""
    actual = panel_long.set_index(["unique_id", "ds"])["y"]
    parts = []
    for origin in origins:
        for name, model in models.items():
            fc = model.forecast(panel_long, exog, origin, horizons)
            fc["model"] = name
            parts.append(fc)
        if verbose:
            print(f"  origin {origin.date()} done", flush=True)
    bt = pd.concat(parts, ignore_index=True)
    bt["y"] = [actual.get((u, d), np.nan) for u, d in zip(bt["unique_id"], bt["ds"])]
    return bt[bt["y"].notna()].reset_index(drop=True)


def summarize(bt: pd.DataFrame, scale: pd.Series | None = None, by=("model",)) -> pd.DataFrame:
    rows = []
    for key, g in bt.groupby(list(by)):
        key = key if isinstance(key, tuple) else (key,)
        rec = dict(zip(by, key))
        rec["n"] = len(g)
        rec["wape"] = wape(g["y"].to_numpy(), g["yhat"].to_numpy())
        rec["bias"] = bias(g["y"].to_numpy(), g["yhat"].to_numpy())
        if scale is not None:
            sc = g["unique_id"].map(scale)
            rec["mase"] = float(np.nanmean(np.abs(g["y"] - g["yhat"]) / sc))
        rows.append(rec)
    return pd.DataFrame(rows).sort_values("wape")


def ensemble(bt: pd.DataFrame, members: list[str], name: str = "ensemble") -> pd.DataFrame:
    """Equal-weight mean of member models (rows aligned on unique_id/origin/h)."""
    sub = bt[bt["model"].isin(members)]
    agg = sub.groupby(["unique_id", "origin", "h", "ds"], as_index=False).agg(yhat=("yhat", "mean"), y=("y", "first"))
    agg["model"] = name
    return agg


def select_best_per_series(bt_sel: pd.DataFrame, candidates: list[str], default: str = "ensemble",
                           margin: float = 0.10) -> pd.Series:
    """Per unique_id: keep the default (ensemble) unless a candidate beats it on the selection window
    by more than `margin` (relative WAPE). The margin guards against noisy 12-origin selection."""
    best = {}
    for uid, g in bt_sel[bt_sel["model"].isin(candidates)].groupby("unique_id"):
        scores: dict[str, float] = {str(m): wape(gg["y"].to_numpy(), gg["yhat"].to_numpy()) for m, gg in g.groupby("model")}
        base: float = scores.get(default, float("inf"))
        challenger = min(scores, key=lambda m: scores[m])
        best[uid] = challenger if scores[challenger] < (1 - margin) * base else default
    return pd.Series(best, name="best_model")


def apply_selection(bt: pd.DataFrame, best: pd.Series, name: str = "selected") -> pd.DataFrame:
    pick = bt[bt.apply(lambda r: best.get(r["unique_id"]) == r["model"], axis=1)].copy()
    pick["model"] = name
    return pick
