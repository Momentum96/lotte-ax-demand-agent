"""No-key Comtrade preview, one month per call; python3 -I script.py [410|156]."""

import csv
import json
import sys
import time
from datetime import date
from pathlib import Path
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

RAW = Path(__file__).resolve().parents[1] / "raw"
BASE = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"


class Partner(TypedDict):
    id: int
    PartnerCodeIsoAlpha3: str
    PartnerDesc: str


class TradeRecord(TypedDict):
    partnerCode: int
    flowCode: str
    cmdCode: str
    partnerDesc: str | None
    netWgt: float | None
    primaryValue: float | None


class Payload(TypedDict):
    data: list[TradeRecord]
    results: list[Partner]
    error: str


def fetch(url: str, path: Path) -> Payload:
    """Cache successful responses; retry transient limits with bounded backoff."""
    if path.exists():
        return json.loads(path.read_text())
    for attempt in range(4):
        try:
            with urlopen(url, timeout=45) as response:
                content = response.read()
            data = json.loads(content)
            if data.get("error") or not ("data" in data or "results" in data):
                raise RuntimeError(str(data))
            path.write_bytes(content)
            time.sleep(4)
            return data
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            print("RETRY", attempt, url, str(error), flush=True)
            if attempt == 3:
                raise
            time.sleep(10 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> None:
    """Fetch monthly bilateral records without silently accepting capped results."""
    reporter = int(sys.argv[1]) if len(sys.argv) > 1 else 410
    RAW.mkdir(parents=True, exist_ok=True)
    cache = RAW / f"comtrade_{reporter}"
    cache.mkdir(exist_ok=True)
    reference = fetch(
        "https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json",
        RAW / "comtrade_partners.json",
    )
    partners = {int(p["id"]): p for p in reference["results"]}
    rows = []
    failures = []
    today = date.today()
    started = time.monotonic()
    for year in range(2012, today.year + 1):
        for month in range(1, 13):
            if (year, month) > (today.year, today.month):
                continue
            period = f"{year}{month:02}"
            params = {
                "reporterCode": reporter, "period": period,
                "cmdCode": "390330,390740", "flowCode": "X,M" if reporter == 410 else "M",
                "partner2Code": 0, "customsCode": "C00", "motCode": 0,
                "maxRecords": 500, "includeDesc": "true",
            }
            if reporter == 156:
                params["partnerCode"] = "0,410"
            try:
                payload = fetch(BASE + "?" + urlencode(params), cache / f"{period}.json")
                records = payload["data"]
                if len(records) >= 500:
                    records = []
                    for flow in ("X", "M"):
                        params["flowCode"] = flow
                        part = fetch(BASE + "?" + urlencode(params), cache / f"{period}_{flow}.json")["data"]
                        if len(part) >= 500:
                            raise RuntimeError(f"Unresolved row cap {period} {flow}")
                        records.extend(part)
                for r in records:
                    code = int(r["partnerCode"])
                    p = partners.get(code, {})
                    rows.append({
                        "date": f"{year}-{month:02}-01", "flow": r["flowCode"],
                        "hs6": r["cmdCode"],
                        "partner_iso3": "WLD" if code == 0 else p.get("PartnerCodeIsoAlpha3", f"UN{code}"),
                        "partner_name": p.get("PartnerDesc", r.get("partnerDesc") or str(code)),
                        "qty_kg": r.get("netWgt"),
                        "value_usd": r.get("primaryValue"),
                    })
                print(reporter, period, len(records), flush=True)
            except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
                failures.append({"period": period, "error": str(error)})
            if time.monotonic() - started > 1150:
                failures.append({"period": "remaining", "error": "20 minute source budget"})
                break
        else:
            continue
        break
    filename = "korea_trade_abs_pc.csv" if reporter == 410 else "china_imports_abs_pc.csv"
    assert rows, "No data; see cached failures"
    keys = [(r["date"], r["flow"], r["hs6"], r["partner_iso3"]) for r in rows]
    assert len(keys) == len(set(keys)), "Duplicate keys"
    rows.sort(key=lambda r: (r["date"], r["flow"], r["hs6"], r["partner_iso3"]))
    with (RAW / filename).open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)
    (cache / "failures.json").write_text(json.dumps(failures, indent=2))
    print("SAVED", filename, len(rows), rows[0]["date"], rows[-1]["date"], flush=True)
    for hs in ("390330", "390740"):
        for flow in ("X", "M") if reporter == 410 else ("M",):
            world = [r for r in rows if r["hs6"] == hs and r["flow"] == flow and r["partner_iso3"] == "WLD"]
            print("LAST SIX WORLD", hs, flow, world[-6:], flush=True)
    if reporter == 410:
        differences = []
        for world in (r for r in rows if r["partner_iso3"] == "WLD"):
            bilateral = [r for r in rows if (r["date"], r["flow"], r["hs6"]) == (world["date"], world["flow"], world["hs6"]) and r["partner_iso3"] != "WLD"]
            total = sum(r["value_usd"] or 0 for r in bilateral)
            value = world["value_usd"] or 0
            if value and abs(total - value) / value > .01:
                differences.append({"date": world["date"], "flow": world["flow"], "hs6": world["hs6"], "world_usd": value, "partners_usd": total})
        (cache / "total_discrepancies.json").write_text(json.dumps(differences, indent=2))
        print("World/partner >1% differences", len(differences), flush=True)


if __name__ == "__main__":
    main()
