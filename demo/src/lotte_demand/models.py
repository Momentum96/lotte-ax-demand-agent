"""Forecasting models. Every model exposes forecast(panel_long, exog, origin, horizons) -> DataFrame
with columns [unique_id, origin, h, ds, yhat, (q10, q90 optional)], using only data with ds <= origin.
"""
from __future__ import annotations

import os
from typing import Any
import warnings
from dataclasses import dataclass, field

# NOTE: torch is never imported in this process. LightGBM and torch each ship an OpenMP runtime and
# loading both in one process deadlocks/segfaults on macOS, so Chronos runs in a worker subprocess.
import numpy as np
import pandas as pd

from .features import build_training_frame, feature_columns

warnings.filterwarnings("ignore", category=FutureWarning)
warnings.filterwarnings("ignore", category=UserWarning)


def _history(panel_long: pd.DataFrame, origin: pd.Timestamp) -> pd.DataFrame:
    return panel_long[panel_long["ds"] <= origin].copy()


def _rows(uid: str, origin: pd.Timestamp, horizons: list[int], yhat: np.ndarray, q10=None, q90=None) -> pd.DataFrame:
    out = pd.DataFrame({
        "unique_id": uid, "origin": origin, "h": horizons,
        "ds": [origin + pd.DateOffset(months=h) for h in horizons],
        "yhat": np.asarray(yhat, dtype=float),
    })
    if q10 is not None:
        out["q10"] = np.asarray(q10, dtype=float)
        out["q90"] = np.asarray(q90, dtype=float)
    return out


class SeasonalNaive:
    name = "seasonal_naive"

    def forecast(self, panel_long, exog, origin, horizons):
        hist = _history(panel_long, origin)
        parts = []
        for uid, g in hist.groupby("unique_id"):
            y = g.sort_values("ds")["y"].to_numpy()
            yhat = [y[-12 + ((h - 1) % 12)] if len(y) >= 12 else y[-1] for h in horizons]
            parts.append(_rows(uid, origin, horizons, yhat))
        return pd.concat(parts, ignore_index=True)


class ETS:
    """AutoETS via statsforecast (per series, seasonal period 12)."""
    name = "ets"

    def forecast(self, panel_long, exog, origin, horizons):
        from statsforecast import StatsForecast
        from statsforecast.models import AutoETS
        hist = _history(panel_long, origin)[["unique_id", "ds", "y"]]
        H = max(horizons)
        sf = StatsForecast(models=[AutoETS(season_length=12)], freq="MS", n_jobs=1)
        fc = sf.forecast(df=hist, h=H, level=[80])
        fc = fc.reset_index() if "unique_id" not in fc.columns else fc
        fc["h"] = fc.groupby("unique_id").cumcount() + 1
        fc = fc[fc["h"].isin(horizons)]
        out = pd.DataFrame({
            "unique_id": fc["unique_id"].values, "origin": origin, "h": fc["h"].values,
            "ds": fc["ds"].values, "yhat": fc["AutoETS"].clip(lower=0).values,
            "q10": fc["AutoETS-lo-80"].clip(lower=0).values, "q90": fc["AutoETS-hi-80"].clip(lower=0).values,
        })
        return out


@dataclass
class LGBMDirect:
    """Global LightGBM, one model per horizon (direct strategy), exog with publication lag."""
    name: str = "lgbm"
    pub_lag: int = 1
    params: dict[str, object] = field(default_factory=lambda: dict(
        objective="regression_l1", learning_rate=0.05, num_leaves=15, min_data_in_leaf=20,
        feature_fraction=0.8, bagging_fraction=0.8, bagging_freq=1, lambda_l2=1.0,
        n_estimators=400, verbose=-1, seed=42,
    ))
    _models: dict[int, tuple[Any, list[str]]] = field(default_factory=dict, repr=False)
    _last_origin: pd.Timestamp | None = None

    def fit(self, panel_long, exog, origin, horizons):
        import lightgbm as lgb
        self._models = {}
        for h in horizons:
            frame = build_training_frame(panel_long, exog, h, self.pub_lag)
            train = frame[(frame["target_ds"] <= origin) & frame["target_scaled"].notna()]
            cols = feature_columns(train)
            model = lgb.LGBMRegressor(**self.params)
            model.fit(train[cols], train["target_scaled"], categorical_feature=["series_code", "hs6_code"])
            self._models[h] = (model, cols)
        self._last_origin = origin

    def forecast(self, panel_long, exog, origin, horizons):
        if self._last_origin != origin or not self._models:
            self.fit(panel_long, exog, origin, horizons)
        parts = []
        for h in horizons:
            model, cols = self._models[h]
            frame = build_training_frame(panel_long, exog, h, self.pub_lag)
            row = frame[frame["ds"] == origin]
            pred = np.clip(model.predict(row[cols]) * row["scale"].to_numpy(), 0, None)
            parts.append(pd.DataFrame({"unique_id": row["unique_id"].values, "origin": origin, "h": h,
                                       "ds": origin + pd.DateOffset(months=h), "yhat": pred}))
        return pd.concat(parts, ignore_index=True)

    def contributions(self, panel_long, exog, origin, h, uid) -> pd.Series:
        """Per-feature contribution (LightGBM pred_contrib) for one series/horizon at origin."""
        if self._last_origin != origin or h not in self._models:
            self.fit(panel_long, exog, origin, list(self._models) or [h])
        model, cols = self._models[h]
        frame = build_training_frame(panel_long, exog, h, self.pub_lag)
        row = frame[(frame["ds"] == origin) & (frame["unique_id"] == uid)]
        contrib = model.predict(row[cols], pred_contrib=True)[0] * float(row["scale"].iloc[0])  # back to tonnes
        s = pd.Series(contrib[:-1], index=cols)
        s["__bias__"] = contrib[-1]
        return s


class ChronosZeroShot:
    """Amazon Chronos-Bolt zero-shot quantile forecasts (no training on our data)."""
    name = "chronos"

    def __init__(self, model_id: str = "amazon/chronos-bolt-small", context: int = 60):
        self.model_id = model_id
        self.context = context
        self._proc = None

    def _worker(self):
        import json
        import subprocess
        import sys
        from pathlib import Path
        if self._proc is None or self._proc.poll() is not None:
            script = Path(__file__).with_name("chronos_worker.py")
            self._proc = subprocess.Popen([sys.executable, str(script), self.model_id], stdin=subprocess.PIPE,
                                          stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, bufsize=1)
            ready = json.loads(self._proc.stdout.readline())
            assert ready.get("ready"), "chronos worker failed to start"
        return self._proc

    def close(self):
        if self._proc is not None and self._proc.poll() is None:
            try:
                self._proc.stdin.write('{"op": "quit"}\n'); self._proc.stdin.flush()
                self._proc.wait(timeout=5)
            except Exception:
                self._proc.kill()

    def forecast(self, panel_long, exog, origin, horizons):
        import json
        proc = self._worker()
        hist = _history(panel_long, origin)
        H = max(horizons)
        uids, ctx = [], []
        for uid, g in hist.groupby("unique_id"):
            y = g.sort_values("ds")["y"].to_numpy(dtype=float)[-self.context:]
            uids.append(uid)
            ctx.append([float(v) for v in y])
        proc.stdin.write(json.dumps({"ctx": ctx, "H": H, "q": [0.1, 0.5, 0.9]}) + "\n"); proc.stdin.flush()
        resp = json.loads(proc.stdout.readline())
        quantiles = np.asarray(resp["quantiles"], dtype=float)  # [n, H, 3]
        parts = []
        idx = [h - 1 for h in horizons]
        for i, uid in enumerate(uids):
            q = quantiles[i]
            parts.append(_rows(uid, origin, horizons, np.clip(q[idx, 1], 0, None), np.clip(q[idx, 0], 0, None), np.clip(q[idx, 2], 0, None)))
        return pd.concat(parts, ignore_index=True)
