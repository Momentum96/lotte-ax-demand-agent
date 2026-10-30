"""Generic no-key UN Comtrade preview downloader (monthly, HS6), one month per call, cached.

Usage: python3 -I download_comtrade.py <reporter> <cmdCodes,comma> <flowCodes,comma> <cache_dirname> [partnerCode]
Example: python3 -I download_comtrade.py 410 870380,852872,841810,845011 comtrade_410_downstream 0
"""
import json
import sys
import time
from datetime import date
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

RAW = Path(__file__).resolve().parents[1] / "raw"
BASE = "https://comtradeapi.un.org/public/v1/preview/C/M/HS"


def fetch(url: str, path: Path) -> dict:
    if path.exists():
        return json.loads(path.read_text())
    for attempt in range(5):
        try:
            with urlopen(url, timeout=60) as response:
                content = response.read()
            data = json.loads(content)
            if data.get("error") or "data" not in data:
                raise RuntimeError(str(data)[:200])
            path.write_bytes(content)
            time.sleep(4)
            return data
        except (HTTPError, URLError, TimeoutError, RuntimeError) as error:
            print("RETRY", attempt, path.name, str(error)[:120], flush=True)
            if attempt == 4:
                raise
            time.sleep(15 * (attempt + 1))
    raise AssertionError("unreachable")


def main() -> None:
    reporter, cmd, flows, dirname = int(sys.argv[1]), sys.argv[2], sys.argv[3], sys.argv[4]
    partner = sys.argv[5] if len(sys.argv) > 5 else None
    cache = RAW / dirname
    cache.mkdir(parents=True, exist_ok=True)
    today = date.today()
    failures = []
    for year in range(2013, today.year + 1):
        for month in range(1, 13):
            if (year, month) > (today.year, today.month):
                continue
            period = f"{year}{month:02}"
            params = {"reporterCode": reporter, "period": period, "cmdCode": cmd, "flowCode": flows,
                      "partner2Code": 0, "customsCode": "C00", "motCode": 0, "maxRecords": 500, "includeDesc": "true"}
            if partner is not None:
                params["partnerCode"] = partner
            try:
                payload = fetch(BASE + "?" + urlencode(params), cache / f"{period}.json")
                n = len(payload.get("data", []))
                print(reporter, dirname, period, n, flush=True)
                if n >= 500:
                    failures.append({"period": period, "error": "row cap 500 hit"})
            except Exception as error:  # noqa: BLE001
                failures.append({"period": period, "error": str(error)[:200]})
    (cache / "failures.json").write_text(json.dumps(failures, indent=2))
    print("DONE", dirname, "failures", len(failures), flush=True)


if __name__ == "__main__":
    main()
