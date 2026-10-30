"""Load public monthly data and build the hierarchical demand panel.

Panel semantics for the preliminary demo:
  - "shipment" proxy  = Korea exports (flow X) of HS 390330 (ABS) and HS 390740 (PC)
  - "customer group"   = destination country (top-N partners + OTH remainder)
  - unit               = tonnes (netWgt / 1000)
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

HS_NAMES = {"390330": "ABS", "390740": "PC"}


def _raw_dir(raw: str | Path | None) -> Path:
    if raw is not None:
        return Path(raw)
    here = Path(__file__).resolve()
    for parent in here.parents:
        candidate = parent / "data" / "raw"
        if candidate.exists():
            return candidate
    raise FileNotFoundError("data/raw not found; pass raw= explicitly")


def load_comtrade_cache(cache_dir: Path, partners_json: Path) -> pd.DataFrame:
    """Parse cached Comtrade preview JSON files into a tidy frame (no CSV dependency)."""
    partners = {int(p["id"]): p for p in json.loads(partners_json.read_text())["results"]}
    rows: list[dict] = []
    for f in sorted(cache_dir.glob("*.json")):
        if f.stem in {"failures", "total_discrepancies"}:
            continue
        payload = json.loads(f.read_text())
        for r in payload.get("data", []):
            code = int(r["partnerCode"])
            p = partners.get(code, {})
            rows.append({
                "date": pd.Timestamp(year=int(r["refYear"]), month=int(r["refMonth"]), day=1),
                "flow": r["flowCode"],
                "hs6": str(r["cmdCode"]),
                "partner_iso3": "WLD" if code == 0 else p.get("PartnerCodeIsoAlpha3", f"UN{code}"),
                "partner_name": p.get("PartnerDesc", r.get("partnerDesc") or str(code)),
                "qty_kg": r.get("netWgt"),
                "value_usd": r.get("primaryValue"),
            })
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.drop_duplicates(subset=["date", "flow", "hs6", "partner_iso3"], keep="last")
    return df.sort_values(["date", "flow", "hs6", "partner_iso3"]).reset_index(drop=True)


def load_trade(raw: str | Path | None = None, reporter: int = 410) -> pd.DataFrame:
    raw_dir = _raw_dir(raw)
    csv = raw_dir / ("korea_trade_abs_pc.csv" if reporter == 410 else "china_imports_abs_pc.csv")
    cache = raw_dir / f"comtrade_{reporter}"
    if cache.exists():
        df = load_comtrade_cache(cache, raw_dir / "comtrade_partners.json")
        if not df.empty:
            return df
    if csv.exists():
        df = pd.read_csv(csv, parse_dates=["date"])
        df["hs6"] = df["hs6"].astype(str)
        return df
    raise FileNotFoundError(f"no trade data for reporter {reporter} in {raw_dir}")


@dataclass
class Panel:
    long: pd.DataFrame          # columns: unique_id, ds, y (tonnes), hs6, partner
    hierarchy: dict[str, list[str]]  # parent_id -> list of child ids (bottom level)
    labels: dict[str, str]           # unique_id -> human label


def build_panel(trade: pd.DataFrame, flow: str = "X", top_n: int = 8,
                select_window_months: int = 36, start: str = "2014-01-01") -> Panel:
    """Build bottom series = hs6 x partner (top_n by recent volume) + OTH remainder.

    Parent series per hs6 = WLD total as reported by Comtrade (kept as reported; the OTH
    remainder absorbs the small world-vs-partner-sum discrepancy so the hierarchy adds up).
    """
    t = trade[(trade["flow"] == flow) & (trade["date"] >= pd.Timestamp(start))].copy()
    t["tonnes"] = pd.to_numeric(t["qty_kg"], errors="coerce").fillna(0.0) / 1000.0
    months = pd.date_range(t["date"].min(), t["date"].max(), freq="MS")
    long_rows = []
    hierarchy: dict[str, list[str]] = {}
    labels: dict[str, str] = {}
    for hs6, g in t.groupby("hs6"):
        name = HS_NAMES.get(hs6, hs6)
        world = g[g["partner_iso3"] == "WLD"].set_index("date")["tonnes"].reindex(months).fillna(0.0)
        bilateral = g[g["partner_iso3"] != "WLD"]
        recent = bilateral[bilateral["date"] >= months[-1] - pd.DateOffset(months=select_window_months - 1)]
        top = (recent.groupby("partner_iso3")["tonnes"].sum().sort_values(ascending=False).head(top_n).index.tolist())
        pname = bilateral.drop_duplicates("partner_iso3").set_index("partner_iso3")["partner_name"].to_dict()
        children = []
        top_sum = pd.Series(0.0, index=months)
        for iso in top:
            s = bilateral[bilateral["partner_iso3"] == iso].set_index("date")["tonnes"].reindex(months).fillna(0.0)
            uid = f"{name}|{iso}"
            children.append(uid)
            labels[uid] = f"{name} -> {pname.get(iso, iso)}"
            top_sum = top_sum + s
            long_rows.append(pd.DataFrame({"unique_id": uid, "ds": months, "y": s.values, "hs6": hs6, "partner": iso}))
        oth = (world - top_sum).clip(lower=0.0)
        uid = f"{name}|OTH"
        children.append(uid)
        labels[uid] = f"{name} -> Other countries"
        long_rows.append(pd.DataFrame({"unique_id": uid, "ds": months, "y": oth.values, "hs6": hs6, "partner": "OTH"}))
        parent = f"{name}|WLD"
        labels[parent] = f"{name} -> World total"
        hierarchy[parent] = children
        long_rows.append(pd.DataFrame({"unique_id": parent, "ds": months, "y": (top_sum + oth).values, "hs6": hs6, "partner": "WLD"}))
    long = pd.concat(long_rows, ignore_index=True)
    return Panel(long=long, hierarchy=hierarchy, labels=labels)


def load_exog(raw: str | Path | None = None, min_coverage: float = 0.6) -> pd.DataFrame:
    """Monthly exogenous drivers indexed by ds (month start). Missing sources are skipped and
    drivers with coverage below min_coverage since 2014 are dropped."""
    raw_dir = _raw_dir(raw)
    frames = []
    if (raw_dir / "brent_monthly.csv").exists():
        b = pd.read_csv(raw_dir / "brent_monthly.csv", parse_dates=["date"]).rename(columns={"date": "ds"})
        frames.append(b.set_index("ds")[["brent_usd"]])
    if (raw_dir / "krw_usd_monthly.csv").exists():
        k = pd.read_csv(raw_dir / "krw_usd_monthly.csv", parse_dates=["date"]).rename(columns={"date": "ds"})
        frames.append(k.set_index("ds")[["krw_per_usd"]])
    # KOSIS industrial production indices (2020=100, unadjusted): manufacturing total, chemicals,
    # rubber&plastics, electronics, electrical equipment, motor vehicles
    if (raw_dir / "kosis_production_index.csv").exists():
        k = pd.read_csv(raw_dir / "kosis_production_index.csv")
        k["ds"] = pd.to_datetime(k["month"] + "-01")
        wide = k.pivot_table(index="ds", columns="industry_code", values="production_index", aggfunc="first")
        wide.columns = [f"kosis_{c}" for c in wide.columns]
        frames.append(wide)
    # Korea monthly automobile production (units), from 산업통상자원부 monthly press releases
    if (raw_dir / "korea_auto_production.csv").exists():
        a = pd.read_csv(raw_dir / "korea_auto_production.csv", parse_dates=["date"]).rename(columns={"date": "ds"})
        a = a.drop_duplicates("ds").set_index("ds")[["production_units"]].rename(columns={"production_units": "auto_production"})
        frames.append(a)
    # downstream-industry proxies: Korea exports of cars / TVs / fridges / washers (tonnes)
    ds_cache = raw_dir / "comtrade_410_downstream"
    if ds_cache.exists():
        d = load_comtrade_cache(ds_cache, raw_dir / "comtrade_partners.json")
        if not d.empty:
            d = d[(d["flow"] == "X") & (d["partner_iso3"] == "WLD")].copy()
            d["tonnes"] = pd.to_numeric(d["qty_kg"], errors="coerce") / 1000.0
            wide = d.pivot_table(index="date", columns="hs6", values="tonnes", aggfunc="sum")
            wide.columns = [f"kor_export_{c}" for c in wide.columns]
            wide.index.name = "ds"
            frames.append(wide)
    # China imports of ABS/PC from world (tonnes) as regional demand pull
    cn_cache = raw_dir / "comtrade_156"
    if cn_cache.exists():
        c = load_comtrade_cache(cn_cache, raw_dir / "comtrade_partners.json")
        if not c.empty:
            c = c[(c["flow"] == "M") & (c["partner_iso3"] == "WLD")].copy()
            c["tonnes"] = pd.to_numeric(c["qty_kg"], errors="coerce") / 1000.0
            wide = c.pivot_table(index="date", columns="hs6", values="tonnes", aggfunc="sum")
            wide.columns = [f"chn_import_{HS_NAMES.get(h, h)}" for h in wide.columns]
            wide.index.name = "ds"
            frames.append(wide)
    if not frames:
        return pd.DataFrame(index=pd.DatetimeIndex([], name="ds"))
    exog = pd.concat(frames, axis=1).sort_index()
    exog.index.name = "ds"
    # drop drivers that are mostly missing over the modelling window (e.g. a source still downloading)
    window = exog.loc[pd.Timestamp("2014-01-01"):]
    keep = [c for c in exog.columns if window[c].notna().mean() >= min_coverage]
    return exog[keep]
