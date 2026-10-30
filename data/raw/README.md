# Public monthly forecasting inputs

Retrieval date: 2026-10-30. All downloads and source responses are untrusted
data, stored only beneath this directory. Do not execute them. Scripts live
in `../scripts`; run Python with `-I` so this directory cannot supply imports.
No API keys, paid APIs, login credentials, or system Python installs are used.

## Reproduction

From `/Users/jgw/OmO Projects/lotte`:

```sh
python3 -I data/scripts/download_fred.py
python3 -I data/scripts/download_trade.py 410
python3 -I data/scripts/download_trade.py 156
python3 -I data/scripts/validate_data.py
```

Run Comtrade reporters sequentially, not concurrently: the public service
returns HTTP 429 under load. Successful JSON responses are cached in
`comtrade_410/` and `comtrade_156/`. Reproduction reuses those exact responses.
For a fresh release, use a separate output snapshot rather than mixing vintages.
`validation_report.json` records observed coverage, monthly gaps, and trade
world-versus-partner discrepancies. No interpolation or zero-filling is used.
Each tidy file has one observation per month **and series/partner key**, not
one row across all industries or trading partners.

## Brent crude

- File: `brent_monthly.csv`; 177 rows, 2012-01-01 through 2026-09-01.
- Download: https://fred.stlouisfed.org/graph/fredgraph.csv?id=MCOILBRENTEU
- Metadata: https://fred.stlouisfed.org/series/MCOILBRENTEU
- Original provider: US Energy Information Administration, via FRED.
- Units: nominal USD per barrel, monthly average, not seasonally adjusted.
- Source response: `fred_MCOILBRENTEU.csv` (includes pre-2012 history).
- Terms: FRED marks this series “Public Domain: Citation Requested” and links
  https://fred.stlouisfed.org/legal/#copyright-public-domain .
- Missing months within delivered range: none. October 2026 is not in the
  retrieved monthly release; this is not a live daily price.

## KRW per US dollar

- File: `krw_usd_monthly.csv`; 177 rows, 2012-01-01 through 2026-09-01.
- Download: https://fred.stlouisfed.org/graph/fredgraph.csv?id=EXKOUS
- Metadata: https://fred.stlouisfed.org/series/EXKOUS
- Original provider: Federal Reserve Board, G.5, via FRED.
- Units: KRW per USD, monthly average, not seasonally adjusted.
- Source response: `fred_EXKOUS.csv` (includes pre-2012 history).
- Terms: FRED marks this series “Public Domain: Citation Requested” and links
  https://fred.stlouisfed.org/legal/#copyright-public-domain .
- Missing months within delivered range: none. October 2026 is not in the
  retrieved release. This is the spot-rate monthly average, not an end-month
  or inverse USD-per-KRW exchange rate.

## ABS and PC merchandise trade

- Endpoint: https://comtradeapi.un.org/public/v1/preview/C/M/HS
- Developer documentation: https://comtradedeveloper.un.org/
- Partner dictionary: https://comtradeapi.un.org/files/v1/app/reference/partnerAreas.json
- Products: HS 390330 ABS; HS 390740 polycarbonates. Classification `HS`
  uses each reporter's original HS revision; revisions are retained in source JSON.
- Korea reporter: 410; exports `X`, imports `M`, all returned partners and World.
- China reporter: 156; optional imports only, partners World (0) and Korea (410).
- Units: `qty_kg` is **netWgt**, not an unchecked supplementary quantity.
  Blank net weight means unavailable, not zero. `value_usd` is `primaryValue`,
  generally export FOB / import CIF; do not treat these valuation bases as equal.
- Dates: monthly reference period, first day added for tidy formatting.
- Actual API constraints: monthly `period` must be YYYYMM, not YYYY; only
  one period is permitted per preview call. Multiple HS and flow codes work.
  A response reaching the 500-row cap is split by flow; a still-capped response
  is recorded as a failure rather than silently accepted as a complete month.
- Terms: UN Comtrade / United Nations source attribution required. Public
  endpoint availability is not an unrestricted redistribution license.
  Consult UN Comtrade terms before external redistribution:
  https://comtradeplus.un.org/ . A historical license PDF URL redirected to the
  application, so no specific open-license identifier was verified.
- Partner identifiers use the official dictionary, with World changed to `WLD`.
  Comtrade special areas can have non-ISO identifiers; unknown codes retain
  `UN<number>` rather than being misidentified as sovereign countries.
- Absent partner-months are not filled with zero. Preview gaps, provisional
  releases, statistical adjustments, and special areas can affect partner sums.
- Coverage and validation results are recorded below after collection finishes.
