"""Collect official domestic vehicle production; run with python3 -I.

Requires Python's standard library and Poppler's pdftotext on PATH.
Raw PDFs, extracted text and an observation ledger are retained for audit.
"""

import csv
import hashlib
import html
import json
import re
import subprocess
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import TypedDict
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode
from urllib.request import urlopen

BASE = "https://www.motir.go.kr"
RAW = Path(__file__).resolve().parents[1] / "raw"
CACHE = RAW / "auto_sources"
NUMBER = r"\d{1,3}(?:,\d{3})+"
MONTH = r"(?<!\d)(\d{2})\.\s*(\d{1,2})\s*월"


class SourceResult(TypedDict):
    published: str
    page_url: str
    pdf_url: str
    title: str
    sha256: str
    observations: list[tuple[str, int, str]]
    error: str


def fetch(url: str) -> bytes:
    """Download one public response, with a bounded network timeout."""
    with urlopen(url, timeout=40) as response:
        return response.read()


def parse(text: str) -> list[tuple[str, int, str]]:
    """Extract exact integers from production charts and explicit total rows."""
    found: list[tuple[str, int, str]] = []
    # Older releases have a chart of 15 monthly observations, in units of cars.
    # Layout positions preserve x order even when labels occur on several lines.
    for chart in re.finditer(r"월별\s*자동차\s*생산[^\n]*", text):
        block = text[chart.end():].split("\f")[0][:7000]
        lines = block.splitlines()
        for index, line in enumerate(lines):
            months = list(re.finditer(MONTH, line))
            if len(months) < 6:
                continue
            values = [
                (m.start() + len(m[0]) / 2, int(m[0].replace(",", "")))
                for prior in lines[:index]
                for m in re.finditer(NUMBER, prior)
                if 100_000 <= int(m[0].replace(",", "")) < 600_000
            ]
            if len(values) != len(months):
                continue
            ordered = sorted(values)
            if any(abs(x - (m.start() + len(m[0]) / 2)) > 6
                   for (x, _), m in zip(ordered, months, strict=True)):
                continue
            for (_, value), month in zip(ordered, months, strict=True):
                found.append((f"20{month[1]}-{int(month[2]):02d}-01",
                              value, "production_chart"))
            break
    # Newer releases have three explicit month columns followed by rates/YTD.
    for match in re.finditer(r"(?m)^\s*생\s*산(?:량)?(?:\(대\))?\s+([^\n]+)", text):
        prefix = text[max(0, match.start() - 1800):match.start()]
        rows = prefix.splitlines()
        headers = [
            list(re.finditer(MONTH, line))
            for line in rows[-16:]
            if len(list(re.finditer(MONTH, line))) >= 3
        ]
        own_headers = [
            line for line in rows[-16:]
            if re.search(r"구\s*분", line) and re.search(MONTH, line)
        ]
        tokens = match[1].split()
        if own_headers and tokens and re.fullmatch(NUMBER, tokens[0]):
            own_header = own_headers[-1]
            dates_on_header = list(re.finditer(MONTH, own_header))
            # In the older format only the first column is a level; following
            # columns are percentage changes, not previous-month observations.
            if len(dates_on_header) < 3:
                month = dates_on_header[0]
                value = int(tokens[0].replace(",", ""))
                if 100_000 <= value < 600_000:
                    found.append((f"20{month[1]}-{int(month[2]):02d}-01",
                                  value, "production_single_month_table"))
                continue
        if not headers:
            continue
        dates = headers[-1][:3]
        if len(tokens) < 3 or not all(re.fullmatch(NUMBER, v) for v in tokens[:3]):
            continue
        for month, token in zip(dates, tokens[:3], strict=True):
            value = int(token.replace(",", ""))
            if 100_000 <= value < 600_000:
                found.append((f"20{month[1]}-{int(month[2]):02d}-01",
                              value, "production_table"))
    return found


def collect(source: tuple[str, str, str, str]) -> SourceResult:
    """Cache a PDF and return its extracted observations or explicit failure."""
    published, page, url, title = source
    identifier = page.split("/")[-2]
    pdf = CACHE / f"auto_{identifier}.pdf"
    try:
        if not pdf.exists():
            content = fetch(url)
            if title.endswith(".hwp"):
                hwp = CACHE / f"auto_{identifier}.hwp"
                hwp.write_bytes(content)
                subprocess.run(["rhwp", "export-pdf", str(hwp), "-o", str(pdf)],
                               capture_output=True, check=True, timeout=45)
            else:
                pdf.write_bytes(content)
        content = pdf.read_bytes()
        info = subprocess.run(["pdfinfo", "-"], input=content,
                              capture_output=True, check=True).stdout.decode()
        size = re.search(r"Page size:\s+([\d.]+) x ([\d.]+)", info)
        crops = [[]]
        if size and float(size[1]) > float(size[2]):
            half, height = int(float(size[1]) / 2), int(float(size[2])) + 1
            crops = [["-x", str(x), "-y", "0", "-W", str(half), "-H", str(height)]
                     for x in (0, half)]
        extracted = "\f".join(
            subprocess.run(["pdftotext", "-layout", *crop, "-", "-"],
                           input=content, capture_output=True,
                           check=True).stdout.decode("utf-8")
            for crop in crops
        )
        (CACHE / f"auto_{identifier}.txt").write_text(extracted, encoding="utf-8")
        observations = parse(extracted)
        return {"published": published, "page_url": page, "pdf_url": url,
                "title": title, "sha256": hashlib.sha256(content).hexdigest(),
                "observations": observations, "error": ""}
    except (HTTPError, URLError, TimeoutError, subprocess.CalledProcessError,
            subprocess.TimeoutExpired) as error:
        return {"published": published, "page_url": page, "pdf_url": url,
                "title": title, "sha256": "", "observations": [], "error": str(error)}


def main() -> None:
    """Discover public releases, retain evidence and write only observed months."""
    CACHE.mkdir(parents=True, exist_ok=True)
    query = urlencode({"searchCondition": "4", "searchKeyword": "자동차",
                       "rowPageC": "1000"})
    listing_url = f"{BASE}/kor/article/ATCL3f49a5a8c?{query}"
    listing = fetch(listing_url).decode("utf-8")
    (CACHE / "auto_listing.html").write_text(listing, encoding="utf-8")
    sources: list[tuple[str, str, str, str]] = []
    for row in re.findall(r"<tr\b[^>]*>(.*?)</tr>", listing, re.S):
        page = re.search(r'href="(/kor/article/ATCL3f49a5a8c/\d+/view)', row)
        published = re.search(r"<td>(20\d{2}-\d{2}-\d{2})</td>", row)
        attachment = re.search(
            r'href="(/attach/down/[^"]+)"\s+title="([^"]+\.pdf) 다운로드"', row,
        ) or re.search(
            r'href="(/attach/down/[^"]+)"\s+title="([^"]+\.hwp) 다운로드"', row,
        )
        if not (page and published and attachment) or published[1] < "2012-01-01":
            continue
        title = html.unescape(attachment[2])
        if "자동차" not in title or not re.search(r"동향|실적|수출|산업", title):
            continue
        sources.append((published[1], BASE + page[1], BASE + attachment[1], title))
    with ThreadPoolExecutor(max_workers=4) as executor:
        results = list(executor.map(collect, sources))
    observations = []
    latest: dict[str, int] = {}
    for result in sorted(results, key=lambda r: (r["published"], r["page_url"])):
        for date, value, method in result["observations"]:
            if date < "2012-01-01":
                continue
            latest[date] = value
            observations.append((date, value, result["published"],
                                 result["page_url"], result["pdf_url"], method))
    with (RAW / "auto_observations.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "production_units", "published", "page_url",
                         "pdf_url", "method"])
        writer.writerows(observations)
    with (RAW / "korea_auto_production.csv").open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(["date", "production_units"])
        writer.writerows(sorted(latest.items()))
    manifest = {"retrieved_at_utc": datetime.now(timezone.utc).isoformat(),
                "listing_url": listing_url, "sources": results}
    (RAW / "auto_manifest.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Sources: {len(results)}, parsed: {sum(bool(r['observations']) for r in results)}")
    print(f"Rows: {len(latest)}, coverage: {min(latest, default='NONE')}.."
          f"{max(latest, default='NONE')}")
    print(f"Download failures: {sum(bool(r['error']) for r in results)}")


if __name__ == "__main__":
    main()
