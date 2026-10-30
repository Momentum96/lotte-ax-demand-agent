"""Download public FRED monthly series. Run: python3 -I data/scripts/download_fred.py."""

import csv
import io
from pathlib import Path
from urllib.request import urlopen

RAW = Path(__file__).resolve().parents[1] / "raw"


def main() -> None:
    """Keep source responses and tidy monthly observations from 2012 onward."""
    RAW.mkdir(parents=True, exist_ok=True)
    for series, filename, column in (
        ("MCOILBRENTEU", "brent_monthly.csv", "brent_usd"),
        ("EXKOUS", "krw_usd_monthly.csv", "krw_per_usd"),
    ):
        url = f"https://fred.stlouisfed.org/graph/fredgraph.csv?id={series}"
        with urlopen(url, timeout=90) as response:
            content = response.read()
        (RAW / f"fred_{series}.csv").write_bytes(content)
        rows = [
            {"date": r["observation_date"], column: float(r[series])}
            for r in csv.DictReader(io.StringIO(content.decode()))
            if r["observation_date"] >= "2012-01-01"
            and r[series] not in ("", ".")
        ]
        assert len({r["date"] for r in rows}) == len(rows)
        assert all(str(r["date"]).endswith("-01") for r in rows)
        with (RAW / filename).open("w", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=["date", column])
            writer.writeheader()
            writer.writerows(rows)
        print(filename, len(rows), rows[0]["date"], rows[-1]["date"])


if __name__ == "__main__":
    main()
