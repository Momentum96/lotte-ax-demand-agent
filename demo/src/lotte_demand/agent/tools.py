"""Deterministic tools the agent calls. Every tool returns JSON-serialisable dicts; the LLM never
computes numbers itself, it only plans, interprets and explains tool outputs.

The workspace is built once per process from the public-data panel and a cached backtest.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import pandas as pd

from ..anomaly import exog_shifts, residual_zscores
from ..backtest import summarize, wape, bias as bias_fn
from ..data import Panel, build_panel, load_exog, load_trade
from ..models import ETS, ChronosZeroShot, LGBMDirect, SeasonalNaive
from ..reconcile import reconcile_mint_ols

HORIZONS = [1, 2, 3, 4, 5, 6]


def _ts(x) -> str:
    return pd.Timestamp(x).strftime("%Y-%m")


@dataclass
class Workspace:
    panel: Panel
    exog: pd.DataFrame
    backtest: pd.DataFrame            # cached rolling backtest (all models incl. 'ensemble', 'selected')
    best_model: dict[str, str]        # per-series selection from the selection window
    artifacts: Path                   # where decisions / handoff files are written
    lgbm: LGBMDirect = field(default_factory=LGBMDirect)
    ets: ETS = field(default_factory=ETS)
    chronos: ChronosZeroShot = field(default_factory=ChronosZeroShot)
    snaive: SeasonalNaive = field(default_factory=SeasonalNaive)
    _fc_cache: dict = field(default_factory=dict)

    @classmethod
    def load(cls, artifacts: str | Path, top_n: int = 6) -> "Workspace":
        artifacts = Path(artifacts)
        trade = load_trade()
        panel = build_panel(trade, flow="X", top_n=top_n)
        exog = load_exog()
        bt = pd.read_parquet(artifacts / "backtest.parquet")
        best = json.loads((artifacts / "best_model.json").read_text())
        return cls(panel=panel, exog=exog, backtest=bt, best_model=best, artifacts=artifacts)

    # ---------- tool 1: ingest / data quality ----------
    def ingest(self, origin: str | None = None) -> dict:
        long = self.panel.long
        origin_ts = pd.Timestamp(origin) if origin else long["ds"].max()
        hist = long[long["ds"] <= origin_ts]
        zero_runs = (hist.assign(z=hist["y"] == 0).groupby("unique_id")["z"].sum()).to_dict()
        ex = self.exog[self.exog.index <= origin_ts]
        return {
            "origin": _ts(origin_ts),
            "series": int(hist["unique_id"].nunique()),
            "months": int(hist["ds"].nunique()),
            "first_month": _ts(hist["ds"].min()),
            "last_actual_month": _ts(hist["ds"].max()),
            "zero_months_per_series": {k: int(v) for k, v in zero_runs.items() if v > 0},
            "drivers": {c: {"last_available": _ts(ex[c].dropna().index.max()) if ex[c].notna().any() else None,
                            "coverage": float(ex[c].notna().mean())} for c in ex.columns},
            "publication_lag_months": 1,
            "quality_gate": "pass" if hist["y"].notna().all() else "fail",
            "unit": "tonnes (Korea exports as shipment proxy; destination country as customer group)",
        }

    # ---------- tool 2: forecast ----------
    def forecast(self, origin: str | None = None, horizons: list[int] | None = None) -> dict:
        long = self.panel.long
        origin_ts = pd.Timestamp(origin) if origin else long["ds"].max()
        H = horizons or HORIZONS
        key = (origin_ts, tuple(H))
        if key not in self._fc_cache:
            parts = {}
            for m in (self.snaive, self.ets, self.lgbm, self.chronos):
                parts[m.name] = m.forecast(long, self.exog, origin_ts, H)
            ens = pd.concat([parts["ets"], parts["lgbm"], parts["chronos"]]).groupby(
                ["unique_id", "origin", "h", "ds"], as_index=False)["yhat"].mean()
            # per-series selection: ensemble unless the selection window said a single model is better
            sel = []
            for uid, g in ens.groupby("unique_id"):
                best = self.best_model.get(uid, "ensemble")
                src = ens if best == "ensemble" else parts[best]
                sel.append(src[src["unique_id"] == uid])
            fc = pd.concat(sel, ignore_index=True)[["unique_id", "origin", "h", "ds", "yhat"]]
            fc = reconcile_mint_ols(fc, self.panel.hierarchy)
            q = parts["chronos"][["unique_id", "h", "q10", "q90"]]
            fc = fc.merge(q, on=["unique_id", "h"], how="left")
            # keep interval width from chronos but centre it on the reconciled point
            c_mid = parts["chronos"].set_index(["unique_id", "h"])["yhat"]
            shift = fc["yhat"].values - c_mid.reindex(list(zip(fc["unique_id"], fc["h"]))).values
            fc["q10"] = np.clip(fc["q10"] + shift, 0, None)
            fc["q90"] = np.clip(fc["q90"] + shift, 0, None)
            fc["model"] = fc["unique_id"].map(lambda u: self.best_model.get(u, "ensemble"))
            self._fc_cache[key] = fc
        fc = self._fc_cache[key]
        rows = [{"unique_id": r.unique_id, "label": self.panel.labels.get(r.unique_id, r.unique_id), "h": int(r.h),
                 "target_month": _ts(r.ds), "forecast_t": round(float(r.yhat), 1),
                 "p10_t": round(float(r.q10), 1) if pd.notna(r.q10) else None,
                 "p90_t": round(float(r.q90), 1) if pd.notna(r.q90) else None, "model": r.model}
                for r in fc.itertuples()]
        return {"origin": _ts(origin_ts), "horizons": H, "n_series": int(fc["unique_id"].nunique()),
                "reconciled": "MinT-OLS (bottom series sum to world totals)", "rows": rows}

    # ---------- tool 3: backtest summary ----------
    def backtest_summary(self, by_series: bool = False) -> dict:
        bt = self.backtest
        rep = bt[bt["window"] == "report"] if "window" in bt.columns else bt
        overall = summarize(rep, by=("model",)).round(4)
        by_h = summarize(rep[rep["model"].isin(["seasonal_naive", "selected"])], by=("model", "h")).round(4)
        out = {"window": {"origins": int(rep["origin"].nunique()), "first_origin": _ts(rep["origin"].min()),
                          "last_origin": _ts(rep["origin"].max()), "horizons": sorted(int(h) for h in rep["h"].unique())},
               "by_model": overall.to_dict(orient="records"),
               "selected_vs_naive_by_h": by_h.to_dict(orient="records")}
        if by_series:
            bs = summarize(rep[rep["model"].isin(["seasonal_naive", "selected"])], by=("unique_id", "model")).round(4)
            out["by_series"] = bs.to_dict(orient="records")
        return out

    # ---------- tool 4: compare with (synthetic) sales forecast ----------
    def sales_forecast(self, origin_ts: pd.Timestamp, H: list[int]) -> pd.DataFrame:
        """SYNTHETIC stand-in for the sales team's manual FCST: trailing-3-month mean with a fixed
        per-series optimism bias (+8%) and no seasonality. Clearly labelled synthetic in the demo."""
        long = self.panel.long
        hist = long[long["ds"] <= origin_ts]
        rows = []
        for uid, g in hist.groupby("unique_id"):
            base = g.sort_values("ds")["y"].tail(3).mean() * 1.08
            for h in H:
                rows.append({"unique_id": uid, "h": h, "ds": origin_ts + pd.DateOffset(months=h), "sales_fcst": base})
        return pd.DataFrame(rows)

    def compare_with_sales_fcst(self, origin: str | None = None, threshold_pct: float = 15.0) -> dict:
        fc_res = self.forecast(origin)
        origin_ts = pd.Timestamp(fc_res["origin"] + "-01")
        fc = self._fc_cache[(origin_ts, tuple(HORIZONS))]
        sales = self.sales_forecast(origin_ts, HORIZONS)
        m = fc.merge(sales, on=["unique_id", "h", "ds"])
        m["diff_pct"] = (m["sales_fcst"] - m["yhat"]) / m["yhat"].replace(0, np.nan) * 100
        m["flag"] = m["diff_pct"].abs() >= threshold_pct
        flagged = m[m["flag"]].sort_values("diff_pct", key=np.abs, ascending=False)
        # surface each series once (its worst horizon) first, then the rest, so proposals cover distinct cells
        first = flagged.drop_duplicates("unique_id", keep="first")
        rest = flagged[~flagged.index.isin(first.index)]
        flagged = pd.concat([first, rest])
        return {"origin": _ts(origin_ts), "threshold_pct": threshold_pct, "n_cells": int(len(m)),
                "n_flagged": int(m["flag"].sum()),
                "note": "sales_fcst is SYNTHETIC in this public-data demo (trailing 3m mean x 1.08)",
                "flagged": [{"unique_id": r.unique_id, "label": self.panel.labels.get(r.unique_id), "h": int(r.h),
                             "target_month": _ts(r.ds), "ai_forecast_t": round(float(r.yhat), 1),
                             "sales_fcst_t": round(float(r.sales_fcst), 1), "diff_pct": round(float(r.diff_pct), 1)}
                            for r in flagged.head(25).itertuples()]}

    # ---------- tool 5: anomalies ----------
    def detect_anomalies(self, origin: str | None = None) -> dict:
        long = self.panel.long
        origin_ts = pd.Timestamp(origin) if origin else long["ds"].max()
        rz = residual_zscores(self.backtest[self.backtest["model"] == "selected"], "selected", origin_ts)
        ex = exog_shifts(self.exog, origin_ts)
        return {"asof": _ts(origin_ts),
                "series_surprises": [{"unique_id": r.unique_id, "label": self.panel.labels.get(r.unique_id),
                                      "actual_t": round(r.actual, 1), "expected_t": round(r.expected, 1),
                                      "z": round(r.z, 2), "flag": bool(r.flag)} for r in rz.head(10).itertuples()],
                "driver_shifts": [{"driver": r.driver, "asof": _ts(r.asof), "last": round(r.last, 2),
                                   "chg_3m_pct": round(r.chg3m * 100, 1), "z": round(r.z, 2), "flag": bool(r.flag)}
                                  for r in ex.itertuples()]}

    # ---------- tool 6: explain ----------
    def explain(self, unique_id: str, h: int = 1, origin: str | None = None, top_k: int = 8) -> dict:
        long = self.panel.long
        origin_ts = pd.Timestamp(origin) if origin else long["ds"].max()
        self.forecast(origin)  # ensures lgbm fitted at this origin
        contrib = self.lgbm.contributions(long, self.exog, origin_ts, h, unique_id)
        base = float(contrib.pop("__bias__"))
        top = contrib.reindex(contrib.abs().sort_values(ascending=False).index).head(top_k)
        hist = long[(long["unique_id"] == unique_id) & (long["ds"] <= origin_ts)].sort_values("ds")["y"]
        return {"unique_id": unique_id, "label": self.panel.labels.get(unique_id), "h": h, "origin": _ts(origin_ts),
                "lgbm_base_t": round(base, 1),
                "top_feature_contributions_t": {k: round(float(v), 1) for k, v in top.items()},
                "recent_actuals_t": [round(float(v), 1) for v in hist.tail(6)],
                "yoy_last_pct": round(float((hist.iloc[-1] / hist.iloc[-13] - 1) * 100), 1) if len(hist) > 13 and hist.iloc[-13] else None,
                "selected_model": self.best_model.get(unique_id, "ensemble")}

    # ---------- tool 7: propose adjustment ----------
    def propose_adjustment(self, unique_id: str, h: int, origin: str | None = None) -> dict:
        cmp_ = self.compare_with_sales_fcst(origin)
        origin_ts = pd.Timestamp(cmp_["origin"] + "-01")
        fc = self._fc_cache[(origin_ts, tuple(HORIZONS))]
        row = fc[(fc["unique_id"] == unique_id) & (fc["h"] == h)].iloc[0]
        sales = self.sales_forecast(origin_ts, [h])
        s = float(sales[sales["unique_id"] == unique_id]["sales_fcst"].iloc[0])
        ai = float(row["yhat"])
        bt = self.backtest
        rep = bt[(bt.get("window", "report") == "report") & (bt["unique_id"] == unique_id) & (bt["model"] == "selected") & (bt["h"] == h)]
        ai_wape = wape(rep["y"].to_numpy(), rep["yhat"].to_numpy()) if len(rep) else None
        ai_bias = bias_fn(rep["y"].to_numpy(), rep["yhat"].to_numpy()) if len(rep) else None
        # weight: trust AI more when its backtest WAPE is low; cap the human/sales weight at 50%
        w_ai = 0.5 if ai_wape is None else float(np.clip(1.0 - ai_wape, 0.5, 0.9))
        proposed = w_ai * ai + (1 - w_ai) * s
        return {"unique_id": unique_id, "label": self.panel.labels.get(unique_id), "h": h, "target_month": _ts(row["ds"]),
                "ai_forecast_t": round(ai, 1), "p10_t": round(float(row["q10"]), 1), "p90_t": round(float(row["q90"]), 1),
                "sales_fcst_t": round(s, 1), "ai_backtest_wape": None if ai_wape is None else round(ai_wape, 3),
                "ai_backtest_bias": None if ai_bias is None else round(ai_bias, 3),
                "weight_on_ai": round(w_ai, 2), "proposed_t": round(float(proposed), 1),
                "status": "proposed (requires planner confirmation)"}

    # ---------- tool 8: record decision ----------
    def record_decision(self, unique_id: str, h: int, decision: str, value_t: float | None = None,
                        note: str = "", origin: str | None = None, actor: str = "planner") -> dict:
        if decision not in ("accept_ai", "accept_proposed", "override"):
            raise ValueError(f"decision must be accept_ai | accept_proposed | override, got {decision!r}")
        if decision == "override" and value_t is None:
            raise ValueError("override requires value_t")
        origin_ts = pd.Timestamp(origin) if origin else self.panel.long["ds"].max()
        path = self.artifacts / "decisions.jsonl"
        rec = {"ts": pd.Timestamp.now().isoformat(timespec="seconds"), "origin": _ts(origin_ts), "unique_id": unique_id,
               "h": int(h), "decision": decision, "value_t": value_t, "note": note, "actor": actor}
        with path.open("a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        return {"recorded": rec, "n_decisions_total": sum(1 for _ in path.open())}

    # ---------- tool 9: handoff ----------
    def handoff(self, origin: str | None = None) -> dict:
        fc_res = self.forecast(origin)
        origin_ts = pd.Timestamp(fc_res["origin"] + "-01")
        fc = self._fc_cache[(origin_ts, tuple(HORIZONS))].copy()
        fc["confirmed_t"] = fc["yhat"]
        fc["source"] = "ai"
        path = self.artifacts / "decisions.jsonl"
        changed: set[tuple[str, int]] = set()  # cells whose confirmed value differs from the AI value
        if path.exists():
            for line in path.read_text().splitlines():
                d = json.loads(line)
                if d["origin"] != _ts(origin_ts):
                    continue
                key = (d["unique_id"], int(d["h"]))
                idx = (fc["unique_id"] == key[0]) & (fc["h"] == key[1])
                value = d.get("value_t")
                if d["decision"] == "accept_proposed" and value is None:
                    # accepting a proposal without a value means "take the tool's proposal"
                    value = self.propose_adjustment(key[0], key[1], origin)["proposed_t"]
                if d["decision"] in ("accept_proposed", "override") and value is not None:
                    fc.loc[idx, "confirmed_t"] = float(value)
                    fc.loc[idx, "source"] = d["decision"]
                    changed.add(key)
                elif d["decision"] == "accept_ai":
                    fc.loc[idx, ["confirmed_t", "source"]] = [fc.loc[idx, "yhat"].iloc[0], "accept_ai"]
                    changed.discard(key)
        # Keep the confirmed FCST coherent (children sum to the parent), like the reconciled AI forecast:
        # changed children re-aggregate the parent; a parent changed alone is split across its children pro rata.
        conflicts = []
        for parent, kids in self.panel.hierarchy.items():
            for h in HORIZONS:
                pidx = (fc["unique_id"] == parent) & (fc["h"] == h)
                kidx = fc["unique_id"].isin(kids) & (fc["h"] == h)
                kids_changed = any((k, h) in changed for k in kids)
                if kids_changed:
                    if (parent, h) in changed:
                        conflicts.append({"unique_id": parent, "h": h, "resolution": "children decisions win; parent re-aggregated"})
                    fc.loc[pidx, "confirmed_t"] = fc.loc[kidx, "confirmed_t"].sum()
                    fc.loc[pidx, "source"] = "reconciled_bottom_up"
                elif (parent, h) in changed:
                    total = fc.loc[kidx, "confirmed_t"].sum()
                    if total > 0:
                        fc.loc[kidx, "confirmed_t"] *= float(fc.loc[pidx, "confirmed_t"].iloc[0]) / total
                        fc.loc[kidx, "source"] = "reconciled_top_down"
        out = fc[["unique_id", "h", "ds", "yhat", "q10", "q90", "confirmed_t", "source"]].rename(columns={"ds": "target_month"})
        out["target_month"] = out["target_month"].dt.strftime("%Y-%m")
        csv_path = self.artifacts / f"confirmed_fcst_{_ts(origin_ts)}.csv"
        out.to_csv(csv_path, index=False)
        return {"origin": _ts(origin_ts), "file": str(csv_path), "n_rows": int(len(out)),
                "n_overridden": int(out["source"].isin(["accept_proposed", "override"]).sum()),
                "n_reconciled": int(out["source"].str.startswith("reconciled").sum()),
                "conflicts": conflicts,
                "world_totals_t": {u: round(float(out[(out["unique_id"] == u) & (out["h"] == 1)]["confirmed_t"].iloc[0]), 1)
                                   for u in self.panel.hierarchy}}


TOOL_SPECS = [
    {"name": "ingest", "description": "Load the monthly demand panel and external drivers up to an origin month and run data-quality checks.",
     "input_schema": {"type": "object", "properties": {"origin": {"type": "string", "description": "YYYY-MM-01; default latest"}}}},
    {"name": "forecast", "description": "Run the ensemble (ETS+LightGBM+Chronos) with per-series model selection and MinT reconciliation for horizons 1-6.",
     "input_schema": {"type": "object", "properties": {"origin": {"type": "string"}}}},
    {"name": "backtest_summary", "description": "Rolling-origin backtest accuracy (WAPE/bias/MASE) by model, by horizon, optionally by series.",
     "input_schema": {"type": "object", "properties": {"by_series": {"type": "boolean"}}}},
    {"name": "compare_with_sales_fcst", "description": "Compare AI forecast with the sales team's FCST cell by cell and list cells above the deviation threshold.",
     "input_schema": {"type": "object", "properties": {"origin": {"type": "string"}, "threshold_pct": {"type": "number"}}}},
    {"name": "detect_anomalies", "description": "Flag series whose latest actual surprised the model (|z|>=2) and external drivers with unusual 3-month moves.",
     "input_schema": {"type": "object", "properties": {"origin": {"type": "string"}}}},
    {"name": "explain", "description": "Feature contributions behind one series' forecast at one horizon, plus recent actuals.",
     "input_schema": {"type": "object", "properties": {"unique_id": {"type": "string"}, "h": {"type": "integer"}}, "required": ["unique_id"]}},
    {"name": "propose_adjustment", "description": "Blend AI forecast and sales FCST for one flagged cell using backtest accuracy; returns a proposal that needs confirmation.",
     "input_schema": {"type": "object", "properties": {"unique_id": {"type": "string"}, "h": {"type": "integer"}}, "required": ["unique_id", "h"]}},
    {"name": "record_decision", "description": "Record the planner's decision for a cell (accept_ai | accept_proposed | override) with value and note.",
     "input_schema": {"type": "object", "properties": {"unique_id": {"type": "string"}, "h": {"type": "integer"}, "decision": {"type": "string"},
                                                       "value_t": {"type": "number"}, "note": {"type": "string"}}, "required": ["unique_id", "h", "decision"]}},
    {"name": "handoff", "description": "Write the confirmed FCST (AI + recorded decisions) as the S&OP input file and return totals.",
     "input_schema": {"type": "object", "properties": {"origin": {"type": "string"}}}},
]


def dispatch(ws: Workspace, name: str, args: dict) -> dict:
    fn = getattr(ws, name)
    return fn(**{k: v for k, v in (args or {}).items() if v is not None})
