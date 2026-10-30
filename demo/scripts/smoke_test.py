import sys, time
import pandas as pd
sys.path.insert(0, "src")
from lotte_demand.data import load_trade, build_panel, load_exog
from lotte_demand.models import SeasonalNaive, ETS, LGBMDirect, ChronosZeroShot
from lotte_demand.reconcile import reconcile_mint_ols

trade = load_trade()
print("trade rows", len(trade), trade["date"].min().date(), trade["date"].max().date())
panel = build_panel(trade, flow="X", top_n=6)
print("panel series", panel.long["unique_id"].nunique(), "months", panel.long["ds"].nunique())
print(panel.labels)
print(panel.long.groupby("unique_id")["y"].mean().round(0).to_string())
exog = load_exog()
print("exog cols", list(exog.columns), exog.index.min().date(), exog.index.max().date())
origin = panel.long["ds"].max() - pd.DateOffset(months=3)
H = [1, 2, 3]
for m in [SeasonalNaive(), ETS(), LGBMDirect(), ChronosZeroShot()]:
    t0 = time.time()
    fc = m.forecast(panel.long, exog, origin, H)
    print(m.name, "rows", len(fc), f"{time.time()-t0:.1f}s")
    print(fc.head(3).to_string())
    if m.name == "lgbm":
        rec = reconcile_mint_ols(fc, panel.hierarchy)
        chk = rec[rec["h"] == 1].set_index("unique_id")["yhat"]
        for p, kids in panel.hierarchy.items():
            print("reconcile check", p, round(chk[p], 1), "sum kids", round(chk[kids].sum(), 1))
        c = m.contributions(panel.long, exog, origin, 1, list(panel.labels)[0])
        print("top contributions", c.drop("__bias__").abs().sort_values(ascending=False).head(6).to_string())
print("SMOKE_OK")
