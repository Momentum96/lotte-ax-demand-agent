# KOSIS production indices

Collected 2026-10-30 (Asia/Seoul). Source last updated 2026-10-30.

## Source and definition

- Official table: https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1F02001
- Table `101 / DT_1F02001`: 시도/산업별 광공업생산지수(2020=100).
- Publisher: 국가데이터처, 광업제조업동향조사.
- Public metadata: https://kosis.kr/statHtml/statHtmlContent.do?orgId=101&tblId=DT_1F02001&vwCd=MT_ZTITLE&dbUser=NSI.&language=ko
- Public browser data endpoint: https://kosis.kr/statHtml/html.do (POST of the public table form).
- Region `A=00` (전국), industry dimension `B`, item `T10`
  (생산지수(원지수)), monthly `M`. These are original/unadjusted indices,
  not seasonally adjusted `T20`. Unit is 2020=100; no rebasing was performed.
- Codes: C 제조업; C20 화학 물질 및 화학제품 제조업; 의약품 제외;
  C22 고무 및 플라스틱제품 제조업;
  C26 전자 부품, 컴퓨터, 영상, 음향 및 통신장비 제조업;
  C28 전기장비 제조업; C30 자동차 및 트레일러 제조업.

## License and attribution

Official usage policy, checked 2026-10-30:
https://kosis.kr/serviceInfo/useGuide.do

The KOSIS policy permits free use, reuse and redistribution of statistical
information, including commercial use, subject to attribution and its stated
exceptions. Cite 국가데이터처, 광업제조업동향조사, KOSIS, the table above,
and access date. Do not misrepresent or arbitrarily change the statistics.
This is the KOSIS statistical-information usage policy, not an asserted CC
or KOGL license designation. The policy's international/North Korea and
publication-specific exceptions do not describe this domestic statistical table.

## Reproduction

Run from the repository root:

```sh
python3 -I data/scripts/kosis_collect.py
```

Python uses only its standard library. Install/running setup of Aside Browser
and the `aside` CLI is a prerequisite; the script invokes `aside repl`.
No API key, account sign-in to KOSIS, or private data source is used.
The browser establishes an ordinary public KOSIS session, waits for the
rendered table, submits the same public form, and closes its tab in `finally`.
Only locally authored JavaScript is executed. Downloaded JSON/HTML is parsed
as data with JSON and DOM parsers, never eval/exec/imported as code.

The script discovers the latest monthly period from the table metadata,
requests every month starting January 2012, and validates the complete
month-by-industry Cartesian product before writing the CSV. It retains the
source numeric text, including three decimals before 2020. A failed download
or incomplete/duplicate panel raises an error before replacing the CSV.

## Verified coverage

`kosis_production_index.csv`: UTF-8, long format, **1,062 data rows**
(1,063 lines including header), **177 months per industry**, January 2012
through September 2026 inclusive. No missing months, duplicates, nonnumeric
values, interpolation, invented values, or substituted series.
September 2026 is the latest source month on the access date.

| Code | Rows | First month | First value | Last month | Last value |
|---|---:|---|---:|---|---:|
| C | 177 | 2012-01 | 87.644 | 2026-09 | 126.9 |
| C20 | 177 | 2012-01 | 86.258 | 2026-09 | 91.2 |
| C22 | 177 | 2012-01 | 97.042 | 2026-09 | 89.4 |
| C26 | 177 | 2012-01 | 61.898 | 2026-09 | 188.8 |
| C28 | 177 | 2012-01 | 86.189 | 2026-09 | 112.9 |
| C30 | 177 | 2012-01 | 101.220 | 2026-09 | 127.6 |

SHA-256:
`bcabaf1418de836c4924868a6670f3c59f8d039a79ca07ca5cbff4d03d2eec85`

`status=provisional` follows the source's column-level `p)` marker.
The collected file marks August and September 2026 provisional (12 rows).
Blank status means no such marker, not a guarantee against future revisions.
The source warns that the latest two months can include provisional values
and annual revisions can change recent years. It also warns that data from
2015 use the tenth industrial classification; use the 2012–2014 segment with
that classification discontinuity in mind. National indices use a chained
Laspeyres formula. No cross-vintage splicing was performed.

Independent validation parsed the written CSV, checked 1,062 unique
month/code keys and 177 observations for each code, and matched the hash above.
All six 2020 monthly averages are within 0.009 of 100 (source rounding).
Python syntax compilation passed. The complete download command exited 0.

## Attempts and failures

1. Direct unauthenticated Python GET obtained the table title, item metadata
   and latest month successfully. Direct POST to `html.do` initially returned
   a Korean abnormal-service access-block notice, then HTTP 500 even after
   cookies, Referer and AJAX headers were included. GET of `treeInfo.do`
   also returned the block notice. These responses were not treated as data.
2. An ordinary browser session rendered the public table and returned valid
   source observations. Spot checks of January 2012 and September 2026 for
   all six codes established the requested series and endpoints.
3. Initial automation against the standalone content page failed because it
   lacked the parent-frame initialization. A metadata-variable scope error
   and an implicit frame-URL error were corrected by loading the complete
   public table, waiting for its table element, and fetching the explicit
   metadata URL. Final isolated Python execution exited 0 and collected
   the complete 1,062-row panel.

Coverage failures: none within 2012-01–2026-09. October 2026 and later months
are not published in the source on this date and are not included. Browser
automation is a reproducibility dependency because plain HTTP access failed.
