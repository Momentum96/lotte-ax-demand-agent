"""Validate tidy files without importing anything from raw. Run with python3 -I."""

import csv
import json
from collections import defaultdict
from datetime import date
from pathlib import Path

RAW = Path(__file__).resolve().parents[1] / "raw"
FILES = {
    "korea_trade_abs_pc.csv": ("flow", "hs6", "partner_iso3"),
    "korea_auto_production.csv": (),
    "kosis_production_index.csv": ("industry_code",),
    "brent_monthly.csv": (),
    "krw_usd_monthly.csv": (),
    "china_imports_abs_pc.csv": ("flow", "hs6", "partner_iso3"),
}


def main() -> None:
    """Report coverage and gaps, and check monthly dates and unique keys."""
    report = {}
    for filename, dimensions in FILES.items():
        path = RAW / filename
        if not path.exists():
            report[filename] = {"status": "not obtained"}
            continue
        with path.open() as handle:
            rows = list(csv.DictReader(handle))
        assert rows, filename
        keys = [(r["date"], *(r[d] for d in dimensions)) for r in rows]
        assert len(keys) == len(set(keys)), f"{filename}: duplicate keys"
        assert all(date.fromisoformat(r["date"]).day == 1 for r in rows)
        months = sorted({r["date"] for r in rows})
        first, last = months[0], months[-1]
        expected = [
            f"{year}-{month:02}-01"
            for year in range(2012, int(last[:4]) + 1)
            for month in range(1, 13)
            if f"{year}-{month:02}-01" <= last
        ]
        report[filename] = {
            "status": "obtained", "rows": len(rows), "first": first, "last": last,
            "missing_months_since_2012": sorted(set(expected) - set(months)),
        }
        if "trade_abs" in filename or "imports_abs" in filename:
            groups = defaultdict(list)
            for row in rows:
                groups[(row["date"], row["flow"], row["hs6"])].append(row)
            differences = []
            for key, group in sorted(groups.items()):
                world = [r for r in group if r["partner_iso3"] == "WLD"]
                if not world:
                    differences.append({"key": key, "issue": "world missing"})
                    continue
                if filename.startswith("china"):
                    continue
                for field in ("qty_kg", "value_usd"):
                    if not world[0][field]:
                        continue
                    total = float(world[0][field])
                    partners = [r for r in group if r["partner_iso3"] != "WLD"]
                    subtotal = sum(float(r[field]) for r in partners if r[field])
                    if total and abs(total - subtotal) / total > .01:
                        differences.append({
                            "key": key, "field": field, "world": total,
                            "partners": subtotal, "relative_difference": abs(total - subtotal) / total,
                        })
            report[filename]["world_partner_discrepancies_over_1pct"] = differences
            for hs in ("390330", "390740"):
                for flow in ("M", "X"):
                    world = [r for r in rows if r["hs6"] == hs and r["flow"] == flow and r["partner_iso3"] == "WLD"]
                    if world:
                        print(filename, hs, flow, json.dumps(world[-6:]))
        print(filename, len(rows), first, last)
    (RAW / "validation_report.json").write_text(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
