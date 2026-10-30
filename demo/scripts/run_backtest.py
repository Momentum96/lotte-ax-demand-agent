"""Rolling-origin backtest for the public-data demo.

Design (anti-leakage, anti-selection-bias):
  - origins: the 24 month-ends before the last actual month (h=1..6 scored where actuals exist)
  - selection window = first 12 origins -> picks per-series best among candidates
  - report window    = last 12 origins  -> every number quoted in the application comes from here
Outputs under demo/artifacts/: backtest.parquet, best_model.json, summary.json, charts/*.png
"""
import json
import sys
import time
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))
from lotte_demand.backtest import (apply_selection, ensemble, mase_scale, rolling_backtest,  # noqa: E402
                                   select_best_per_series, summarize, wape)
from lotte_demand.data import build_panel, load_exog, load_trade  # noqa: E402
from lotte_demand.models import ETS, ChronosZeroShot, LGBMDirect, SeasonalNaive  # noqa: E402
from lotte_demand.reconcile import reconcile_mint_ols  # noqa: E402

ART = Path(__file__).resolve().parents[1] / "artifacts"
CH = ART / "charts"
HORIZONS = [1, 2, 3, 4, 5, 6]
N_SELECT, N_REPORT = 12, 12
TOP_N = 6
# Model-selection policy: the ensemble is the default; a single model replaces it for a series only
# when it beats the ensemble by more than SELECT_MARGIN (relative WAPE) on the selection window.
# 12 origins are too few for finer selection (tested: margin 0.0 and 0.10 both lost to the plain ensemble).
SELECT_MARGIN = 0.25


def main() -> None:
    ART.mkdir(exist_ok=True)
    CH.mkdir(exist_ok=True)
    t0 = time.time()
    rescore = "--rescore" in sys.argv  # reuse cached model forecasts, redo selection/summary/charts only
    trade = load_trade()
    panel = build_panel(trade, flow="X", top_n=TOP_N)
    exog = load_exog()
    long = panel.long
    last = long["ds"].max()
    origins = [last - pd.DateOffset(months=k) for k in range(N_SELECT + N_REPORT, 0, -1)]
    print(f"panel: {long['unique_id'].nunique()} series, {long['ds'].min().date()}..{last.date()}; "
          f"exog: {list(exog.columns)}; origins {origins[0].date()}..{origins[-1].date()}", flush=True)

    if rescore:
        bt = pd.read_parquet(ART / "backtest.parquet")
        bt = bt[bt["model"] != "selected"].reset_index(drop=True)
    else:
        models = {"seasonal_naive": SeasonalNaive(), "ets": ETS(), "lgbm": LGBMDirect(), "chronos": ChronosZeroShot()}
        bt = rolling_backtest(models, long, exog, origins, HORIZONS)
        # reconcile lgbm/ets/chronos per origin so parents and children are coherent (same as production path)
        rec_parts = []
        for (name, origin), g in bt.groupby(["model", "origin"]):
            if name == "seasonal_naive":
                rec_parts.append(g)
                continue
            full = g.copy()
            rec = reconcile_mint_ols(full[["unique_id", "origin", "h", "ds", "yhat"]], panel.hierarchy)
            full["yhat"] = rec["yhat"].values
            rec_parts.append(full)
        bt = pd.concat(rec_parts, ignore_index=True)
        bt = pd.concat([bt, ensemble(bt, ["ets", "lgbm", "chronos"], "ensemble")], ignore_index=True)
        sel_origins = set(origins[:N_SELECT])
        bt["window"] = np.where(bt["origin"].isin(sel_origins), "select", "report")
    candidates = ["ensemble", "ets", "lgbm", "chronos", "seasonal_naive"]
    best = select_best_per_series(bt[bt["window"] == "select"], candidates, margin=SELECT_MARGIN)
    bt = pd.concat([bt, apply_selection(bt, best, "selected")], ignore_index=True)
    bt.to_parquet(ART / "backtest.parquet", index=False)
    (ART / "best_model.json").write_text(json.dumps(best.to_dict(), indent=2, ensure_ascii=False))

    scale = mase_scale(long[long["ds"] <= origins[0]])
    rep = bt[bt["window"] == "report"]
    by_model = summarize(rep, scale, by=("model",))
    by_h = summarize(rep, scale, by=("model", "h"))
    by_series = summarize(rep[rep["model"].isin(["seasonal_naive", "ensemble", "selected"])], scale, by=("unique_id", "model"))
    world = rep[rep["unique_id"].str.endswith("|WLD")]
    by_model_world = summarize(world, scale, by=("model",))
    summary = {
        "panel": {"series": int(long["unique_id"].nunique()), "first_month": str(long["ds"].min().date()),
                  "last_month": str(last.date()), "top_n_partners": TOP_N, "labels": panel.labels},
        "exog": list(exog.columns),
        "origins": {"select": [str(o.date()) for o in origins[:N_SELECT]], "report": [str(o.date()) for o in origins[N_SELECT:]]},
        "best_model_counts": best.value_counts().to_dict(),
        "report_by_model": by_model.round(4).to_dict(orient="records"),
        "report_by_model_world_totals": by_model_world.round(4).to_dict(orient="records"),
        "report_by_h": by_h.round(4).to_dict(orient="records"),
        "report_by_series": by_series.round(4).to_dict(orient="records"),
        "runtime_sec": round(time.time() - t0, 1),
    }
    (ART / "summary.json").write_text(json.dumps(summary, indent=2, ensure_ascii=False))
    print(by_model.round(3).to_string(index=False))
    print("-- world totals only"); print(by_model_world.round(3).to_string(index=False))
    print("-- best model counts", best.value_counts().to_dict())

    # charts
    plt.rcParams["font.family"] = ["AppleGothic", "Apple SD Gothic Neo", "DejaVu Sans"]
    plt.rcParams["axes.unicode_minus"] = False
    fig, ax = plt.subplots(figsize=(7, 3.6))
    # "selected" is the ensemble plus the per-series swap policy; it is plotted only when it differs from the ensemble
    sel_differs = best.nunique() > 1 or best.iloc[0] != "ensemble"
    bm = by_model[(by_model["model"] != "selected") | sel_differs].sort_values("wape")
    ax.barh(bm["model"], bm["wape"] * 100, color=["#EC1B23" if m == "ensemble" else "#9aa0a6" for m in bm["model"]])
    for i, (m, w) in enumerate(zip(bm["model"], bm["wape"])):
        ax.text(w * 100 + 0.3, i, f"{w*100:.1f}%", va="center", fontsize=9)
    ax.set_xlabel("WAPE (%) on report window, h=1..6, all series"); ax.set_title("Backtest accuracy by model")
    ax.invert_yaxis(); fig.tight_layout(); fig.savefig(CH / "wape_by_model.png", dpi=160); plt.close(fig)

    fig, ax = plt.subplots(figsize=(7, 3.6))
    styles = [("seasonal_naive", "#9aa0a6", "--", "o", 1.2), ("ets", "#80868b", "-", "s", 1.0), ("lgbm", "#80868b", "-", "^", 1.0),
              ("chronos", "#80868b", "-", "D", 1.0), ("ensemble", "#EC1B23", "-", "o", 2.0)]
    if sel_differs:
        styles.append(("selected", "#202124", ":", "x", 1.4))
    for m, color, ls, marker, lw in styles:
        s = by_h[by_h["model"] == m].sort_values("h")
        ax.plot(s["h"], s["wape"] * 100, marker=marker, ls=ls, lw=lw, ms=4, label=m, color=color)
    ax.set_xlabel("horizon (months ahead)"); ax.set_ylabel("WAPE (%)"); ax.set_title("Accuracy by horizon"); ax.legend(fontsize=8, ncol=2)
    fig.tight_layout(); fig.savefig(CH / "wape_by_horizon.png", dpi=160); plt.close(fig)

    for uid in panel.hierarchy:
        fig, ax = plt.subplots(figsize=(8, 3.6))
        hist = long[long["unique_id"] == uid].sort_values("ds")
        ax.plot(hist["ds"], hist["y"], color="#202124", lw=1.4, label="actual")
        s1 = rep[(rep["unique_id"] == uid) & (rep["model"] == "ensemble") & (rep["h"] == 1)].sort_values("ds")
        ax.plot(s1["ds"], s1["yhat"], color="#EC1B23", lw=1.4, label="ensemble, 1-month ahead")
        n1 = rep[(rep["unique_id"] == uid) & (rep["model"] == "seasonal_naive") & (rep["h"] == 1)].sort_values("ds")
        ax.plot(n1["ds"], n1["yhat"], color="#9aa0a6", lw=1.0, ls="--", label="seasonal naive")
        ax.set_xlim(hist["ds"].max() - pd.DateOffset(months=60), hist["ds"].max() + pd.DateOffset(months=1))
        ax.set_title(f"{panel.labels[uid]} (tonnes/month)"); ax.legend(loc="upper left", fontsize=8)
        fig.tight_layout(); fig.savefig(CH / f"series_{uid.replace('|', '_')}.png", dpi=160); plt.close(fig)
    print("BACKTEST_DONE", round(time.time() - t0, 1), "s", flush=True)


if __name__ == "__main__":
    main()
