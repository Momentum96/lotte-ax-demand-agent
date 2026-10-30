"""Collect public KOSIS monthly original production indices, without an API key.

Run: python -I data/scripts/kosis_collect.py
Requires an installed, running Aside Browser and its `aside` CLI. The browser
supplies an ordinary public session, not an API credential. Only returned JSON
data is parsed; no downloaded code is executed. All opened tabs are closed.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
from pathlib import Path
import subprocess
from typing import Final

ROOT: Final = Path(__file__).resolve().parents[1]
CODES: Final = ("C", "C20", "C22", "C26", "C28", "C30")
JS: Final = r"""
const tab = await openTab('https://kosis.kr/statHtml/statHtml.do?orgId=101&tblId=DT_1F02001');
try {
  await snapshot(tab, {interactive:true});
  await tab.frameLocator('#iframe_rightMenu').frameLocator('iframe').locator('#mainTable').waitFor({state:'attached',timeout:60000});
  const result = await tab.evaluate(async () => {
    const w = document.querySelector('#iframe_rightMenu').contentWindow.document.querySelector('iframe').contentWindow;
    const pageText = await (await w.fetch('/statHtml/statHtmlContent.do?orgId=101&tblId=DT_1F02001&vwCd=MT_ZTITLE&dbUser=NSI.&language=ko')).text();
    const info = JSON.parse(pageText.match(/var g_jsonStatInfo\s*=\s*'(.*?)';/s)[1]);
    const codes = ['C','C20','C22','C26','C28','C30'];
    const names = {
      '제조업':'C',
      '화학 물질 및 화학제품 제조업; 의약품 제외':'C20',
      '고무 및 플라스틱제품 제조업':'C22',
      '전자 부품, 컴퓨터, 영상, 음향 및 통신장비 제조업':'C26',
      '전기장비 제조업':'C28',
      '자동차 및 트레일러 제조업':'C30'
    };
    const months = info.periodInfo.listM.filter(x => x >= '201201');
    if (!months.length) throw new Error('No monthly coverage in metadata');
    const fields = [
      {targetId:'PRD',targetValue:'',prdValue:'M,'+months.join(',')+',@'},
      {targetId:'ITM_ID',targetValue:'T10',prdValue:''},
      {targetId:'OV_L1_ID',targetValue:'00',prdValue:''},
      ...codes.map(x=>({targetId:'OV_L2_ID',targetValue:x,prdValue:''}))
    ];
    const params = new URLSearchParams(w.$('#ParamInfo').serialize());
    params.set('fieldList', JSON.stringify(fields));
    params.set('rowAxis','A,B');
    params.set('colAxis','TIME,ITEM');
    params.set('orderStr','OV_L1_ID,OV_L2_ID,TIME,CHAR_ITM_ID');
    params.set('startNum','1');
    params.set('endNum',String(months.length*6));
    params.set('itemMultiply',String(months.length*6));
    params.set('funcPrdSe','M');
    params.set('viewKind','1');
    const response = await w.fetch('/statHtml/html.do',{method:'POST',body:params});
    if (!response.ok) throw new Error('HTTP '+response.status);
    const data = await response.json();
    if (!data.result?.[0]) throw new Error(JSON.stringify(data));
    const doc = new DOMParser().parseFromString(data.result[0],'text/html');
    const headers = [...doc.querySelectorAll('thead th[code="TIME"]')];
    const periods = headers.map(x=>x.getAttribute('name').slice(1));
    const flags = headers.map(x=>x.getAttribute('title').includes('p)')?'provisional':'');
    if (periods.length !== months.length) throw new Error('Truncated month columns');
    const rows = [];
    for (const tr of doc.querySelectorAll('tbody tr')) {
      const cells = [...tr.querySelectorAll('td.value')];
      if (!cells.length) continue;
      const labels = [...tr.querySelectorAll('td.trHeader')].map(x=>x.title);
      const code = names[labels[1]];
      if (!code || labels[0] !== '전국') throw new Error('Unexpected series '+labels);
      if (cells.length !== periods.length) throw new Error('Incomplete series '+code);
      cells.forEach((cell,i)=>rows.push({
        date:periods[i].slice(0,4)+'-'+periods[i].slice(4)+'-01',
        industry_code:code, industry_name:labels[1],
        index_value:cell.title, status:flags[i]
      }));
    }
    return {table:info.tblId,title:info.tblNm,unit:info.unitNm,
      renewal_date:info.renewalDate,latest:info.periodInfo.endM,
      notes:data.result[1],rows};
  });
  console.log('KOSIS_RESULT='+JSON.stringify(result));
} finally {
  await closeTab(tab);
}
"""


def main() -> None:
    """Download, validate a complete monthly panel, and write the verified CSV."""
    proc = subprocess.run(
        ["aside", "repl", JS], capture_output=True, text=True,
        timeout=180, check=True,
    )
    lines = [line for line in proc.stdout.splitlines() if "KOSIS_RESULT=" in line]
    if len(lines) != 1:
        raise RuntimeError(proc.stdout + proc.stderr)
    data = json.loads(lines[0].split("KOSIS_RESULT=", 1)[1])
    rows = data["rows"]
    latest = data["latest"]
    expected_months = {
        f"{year:04}-{month:02}-01"
        for year in range(2012, int(latest[:4]) + 1)
        for month in range(1, 13)
        if f"{year:04}{month:02}" <= latest
    }
    expected = {(month, code) for month in expected_months for code in CODES}
    keys = {(row["date"], row["industry_code"]) for row in rows}
    if keys != expected or len(rows) != len(expected):
        raise RuntimeError(f"Incomplete/duplicate panel: {len(rows)} rows, {len(expected)} expected")
    for row in rows:
        value = float(row["index_value"])
        if not 0 <= value < float("inf"):
            raise RuntimeError(f"Invalid observation: {row}")
        row.update(region_code="00", unit="2020=100", adjustment="unadjusted",
                   source_table="DT_1F02001")
    rows.sort(key=lambda row: (row["date"], CODES.index(row["industry_code"])))
    columns = ["date", "industry_code", "industry_name", "index_value"]
    buffer = io.StringIO(newline="")
    writer = csv.DictWriter(buffer, fieldnames=columns, lineterminator="\n")
    writer.writeheader()
    writer.writerows({column: row[column] for column in columns} for row in rows)
    content = buffer.getvalue().encode("utf-8")
    target = ROOT / "raw" / "kosis_production_index.csv"
    target.parent.mkdir(parents=True, exist_ok=True)
    (target.parent / "kosis_source.json").write_text(json.dumps(data, ensure_ascii=False))
    target.write_bytes(content)
    print(f"{target}: {len(rows)} rows; {min(expected_months)}..{max(expected_months)}")
    print("sha256=" + hashlib.sha256(content).hexdigest())
    for code in CODES:
        series = [r for r in rows if r["industry_code"] == code]
        print(code, len(series), series[0]["index_value"],
              series[-1]["index_value"])
    print("source_updated=" + str(data["renewal_date"]))
    print("source_notes=" + data["notes"])


if __name__ == "__main__":
    main()
