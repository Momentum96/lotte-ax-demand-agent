"""Anomaly signals: forecast-residual z-scores on the latest actual, and exogenous regime shifts."""
from __future__ import annotations

import numpy as np
import pandas as pd


def residual_zscores(bt: pd.DataFrame, model: str, latest_ds: pd.Timestamp, window: int = 24) -> pd.DataFrame:
    """Use 1-step-ahead backtest residuals to standardize the latest month's surprise per series."""
    g = bt[(bt["model"] == model) & (bt["h"] == 1)].copy()
    g["resid"] = g["y"] - g["yhat"]
    rows = []
    for uid, s in g.groupby("unique_id"):
        s = s.sort_values("ds")
        hist = s[s["ds"] < latest_ds].tail(window)
        last = s[s["ds"] == latest_ds]
        if last.empty or len(hist) < 6:
            continue
        sd = float(hist["resid"].std(ddof=1)) or 1.0
        z = float(last["resid"].iloc[0] / sd)
        rows.append({"unique_id": uid, "ds": latest_ds, "actual": float(last["y"].iloc[0]),
                     "expected": float(last["yhat"].iloc[0]), "z": z, "flag": abs(z) >= 2.0})
    return pd.DataFrame(rows).sort_values("z", key=np.abs, ascending=False) if rows else pd.DataFrame(
        columns=["unique_id", "ds", "actual", "expected", "z", "flag"])


def exog_shifts(exog: pd.DataFrame, asof: pd.Timestamp, window: int = 36) -> pd.DataFrame:
    """Flag drivers whose latest 3-month change is unusual vs. their own history (|z| >= 2)."""
    rows = []
    ex = exog[exog.index <= asof]
    for col in ex.columns:
        s = ex[col].dropna()
        if len(s) < window:
            continue
        chg = s.pct_change(3, fill_method=None).dropna()
        hist = chg.iloc[-window:-1]
        if hist.std(ddof=1) == 0 or np.isnan(hist.std(ddof=1)):
            continue
        z = float((chg.iloc[-1] - hist.mean()) / hist.std(ddof=1))
        rows.append({"driver": col, "asof": s.index[-1], "last": float(s.iloc[-1]), "chg3m": float(chg.iloc[-1]),
                     "z": z, "flag": abs(z) >= 2.0})
    return pd.DataFrame(rows).sort_values("z", key=np.abs, ascending=False) if rows else pd.DataFrame(
        columns=["driver", "asof", "last", "chg3m", "z", "flag"])
