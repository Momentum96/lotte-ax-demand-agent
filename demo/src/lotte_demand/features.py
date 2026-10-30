"""Feature engineering for the direct multi-horizon LightGBM model.

Leakage rule: every feature for a row with origin t uses only values observed at or before t.
Exogenous drivers enter with an explicit publication lag (default 1 month: the value for
month t is assumed known one month later, so at origin t we use exog[t-1]).

Scale rule: series differ by two orders of magnitude, so level features and the target are divided
by the series' trailing-12-month mean at the origin ("scale"); the model learns relative dynamics and
predictions are multiplied back by the same scale.
"""
from __future__ import annotations

import numpy as np
import pandas as pd

LAGS = (1, 2, 3, 6, 12)
WINDOWS = (3, 6, 12)


def add_series_features(df: pd.DataFrame) -> pd.DataFrame:
    """df: long panel for ONE unique_id sorted by ds with column y. Adds scaled lag/rolling feats at origin t."""
    out = df.copy()
    y = out["y"]
    scale = y.rolling(12, min_periods=3).mean().clip(lower=1e-6)
    out["scale"] = scale
    out["log_scale"] = np.log1p(scale)
    for lag in LAGS:
        out[f"lag_{lag}"] = y.shift(lag - 1) / scale  # lag_1 == value at origin t itself (relative to level)
    for w in WINDOWS:
        out[f"rmean_{w}"] = y.rolling(w, min_periods=1).mean() / scale
        out[f"rstd_{w}"] = y.rolling(w, min_periods=2).std() / scale
    out["yoy"] = y / y.shift(12).replace(0, np.nan) - 1.0
    out["mom"] = y / y.shift(1).replace(0, np.nan) - 1.0
    return out


def add_calendar(df: pd.DataFrame, horizon: int) -> pd.DataFrame:
    target_month = (df["ds"] + pd.DateOffset(months=horizon)).dt.month
    out = df.copy()
    out["target_month"] = target_month
    out["target_sin"] = np.sin(2 * np.pi * target_month / 12)
    out["target_cos"] = np.cos(2 * np.pi * target_month / 12)
    return out


def align_exog(df: pd.DataFrame, exog: pd.DataFrame, pub_lag: int = 1) -> pd.DataFrame:
    """Join exog values observable at origin t (i.e. exog at t - pub_lag) as 3m/12m relative changes."""
    if exog is None or exog.empty:
        return df
    ex = exog.copy()
    ex.index = ex.index + pd.DateOffset(months=pub_lag)  # value for month m becomes visible at m+pub_lag
    ex_chg3 = ex.pct_change(3, fill_method=None).add_suffix("_chg3")
    ex_chg12 = ex.pct_change(12, fill_method=None).add_suffix("_chg12")
    ex_lvl = (ex / ex.rolling(24, min_periods=6).mean()).add_suffix("_rel24")
    ex_all = pd.concat([ex_chg3, ex_chg12, ex_lvl], axis=1)
    return df.merge(ex_all, left_on="ds", right_index=True, how="left")


def build_training_frame(panel_long: pd.DataFrame, exog: pd.DataFrame, horizon: int,
                         pub_lag: int = 1) -> pd.DataFrame:
    """Rows = (unique_id, origin ds); target = y at ds + horizon (scaled by origin-time level)."""
    parts = []
    for uid, g in panel_long.sort_values("ds").groupby("unique_id", sort=False):
        f = add_series_features(g)
        f["target"] = f["y"].shift(-horizon)
        f["target_scaled"] = f["target"] / f["scale"]
        f["target_ds"] = f["ds"] + pd.DateOffset(months=horizon)
        parts.append(f)
    frame = pd.concat(parts, ignore_index=True)
    frame = add_calendar(frame, horizon)
    frame = align_exog(frame, exog, pub_lag=pub_lag)
    frame["series_code"] = frame["unique_id"].astype("category")
    frame["hs6_code"] = frame["hs6"].astype("category")
    return frame


def feature_columns(frame: pd.DataFrame) -> list[str]:
    drop = {"unique_id", "ds", "y", "hs6", "partner", "target", "target_scaled", "target_ds", "scale"}
    return [c for c in frame.columns if c not in drop]
