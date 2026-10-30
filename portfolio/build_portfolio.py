"""Build the task-specific portfolio PDF:
  [front 3 pages: 과제 해결방안 요약 / 예선 데모 결과 / 에이전트 프로토타입]
  + existing 9-page portfolio (전건우_포트폴리오_260922.pdf)
  + appendix (award certificates, paper first pages)
Numbers are read from demo/artifacts/summary.json so the PDF never drifts from the backtest.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from PIL import Image
from pypdf import PdfReader, PdfWriter
from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfbase.ttfonts import TTFont
from reportlab.platypus import (Flowable, Image as RLImage, PageBreak, Paragraph, SimpleDocTemplate, Spacer,
                                Table, TableStyle)

ROOT = Path(__file__).resolve().parents[1]
ART = ROOT / "demo" / "artifacts"
OUT = ROOT / "portfolio" / "out"
OUT.mkdir(exist_ok=True)
FRONT = OUT / "front_pages.pdf"
APPX = OUT / "appendix.pdf"
FINAL = OUT / "전건우_포트폴리오_LOTTE_AX_수요예측.pdf"
EXISTING = Path.home() / "Desktop/Jeon/GeonWoo/Jobs/latest/전건우_포트폴리오_260922.pdf"
AWARDS = Path.home() / "Desktop/Jeon/GeonWoo/Jobs/이력서용 자료/수상실적"
SHOTS = ROOT / "demo" / "artifacts" / "screenshots"
CONTENT_WIDTH = 186 * mm

pdfmetrics.registerFont(TTFont("Paperlogy", str(Path.home() / "Library/Fonts/Paperlogy-4Regular.ttf")))
pdfmetrics.registerFont(TTFont("PaperlogySemiBold", str(Path.home() / "Library/Fonts/Paperlogy-6SemiBold.ttf")))
RED = colors.HexColor("#EC1B23")


def styles():
    b = getSampleStyleSheet()["Normal"]
    mk = lambda n, f, s, l, c="#222222": ParagraphStyle(n, parent=b, fontName=f, fontSize=s, leading=l, textColor=colors.HexColor(c))
    return {"title": mk("t", "PaperlogySemiBold", 17, 21), "section": mk("s", "PaperlogySemiBold", 11.5, 14.5),
            "meta": mk("m", "Paperlogy", 7.5, 10, "#555555"), "body": mk("b", "Paperlogy", 8.1, 11.2),
            "small": mk("sm", "Paperlogy", 7.8, 10.2), "label": mk("l", "PaperlogySemiBold", 7.4, 9.6),
            "th": mk("th", "PaperlogySemiBold", 6.6, 8.4), "td": mk("td", "Paperlogy", 6.5, 8.2),
            "kpi": mk("k", "PaperlogySemiBold", 15, 18, "#EC1B23"), "kpil": mk("kl", "Paperlogy", 6.8, 8.6, "#555555")}


def P(text, st):
    return Paragraph(text.replace("\n", "<br/>"), st)


def grid(rows, widths, st, header=True):
    t = Table([[P(str(c), st["th"] if (header and r == 0) else st["td"]) for c in row] for r, row in enumerate(rows)], colWidths=widths, repeatRows=1 if header else 0)
    ts = [("GRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#c2c2c2")), ("VALIGN", (0, 0), (-1, -1), "TOP"),
          ("TOPPADDING", (0, 0), (-1, -1), 2.5), ("BOTTOMPADDING", (0, 0), (-1, -1), 2.5), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]
    if header:
        ts += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#f1f1f1")), ("LINEBELOW", (0, 0), (-1, 0), 0.6, colors.HexColor("#555555"))]
    t.setStyle(TableStyle(ts))
    return t


def labeled(rows, st, label_w=22 * mm):
    t = Table([[P(k, st["label"]), P(v, st["small"])] for k, v in rows], colWidths=[label_w, CONTENT_WIDTH - label_w])
    t.setStyle(TableStyle([("GRID", (0, 0), (-1, -1), 0.25, colors.HexColor("#d0d0d0")), ("BACKGROUND", (0, 0), (0, -1), colors.HexColor("#f4f4f4")),
                           ("VALIGN", (0, 0), (-1, -1), "TOP"), ("TOPPADDING", (0, 0), (-1, -1), 3), ("BOTTOMPADDING", (0, 0), (-1, -1), 3), ("LEFTPADDING", (0, 0), (-1, -1), 3), ("RIGHTPADDING", (0, 0), (-1, -1), 3)]))
    return t


def img(path, max_w, max_h=None):
    with Image.open(path) as im:
        w, h = im.size
    scale = max_w / w
    if max_h and h * scale > max_h:
        scale = max_h / h
    return RLImage(str(path), width=w * scale, height=h * scale)


def kpi_row(items, st):
    cells = [[[P(v, st["kpi"])], [P(l, st["kpil"])]] for v, l in items]  # each inner table: 2 rows x 1 col
    t = Table([[Table(c, colWidths=[(CONTENT_WIDTH / len(items)) - 4 * mm]) for c in cells]], colWidths=[CONTENT_WIDTH / len(items)] * len(items))
    t.setStyle(TableStyle([("BOX", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0d0d0")), ("INNERGRID", (0, 0), (-1, -1), 0.3, colors.HexColor("#d0d0d0")), ("TOPPADDING", (0, 0), (-1, -1), 4), ("BOTTOMPADDING", (0, 0), (-1, -1), 4)]))
    return t


def _page_number(prefix: str):
    def draw(canvas, doc):
        canvas.saveState(); canvas.setFont("Paperlogy", 7); canvas.setFillColor(colors.HexColor("#777777"))
        canvas.drawRightString(A4[0] - 14 * mm, 8 * mm, f"{prefix}-{doc.page}")
        canvas.drawString(14 * mm, 8 * mm, "LOTTE AX Challenge 2026 · 롯데케미칼 첨단소재 수요예측 AI 기획 · 전건우")
        canvas.restoreState()
    return draw


page_number = _page_number("A")
appendix_page_number = _page_number("B")


def build_front(summary: dict[str, Any], cycle: dict[str, Any] | None) -> None:
    st = styles()
    bm = {r["model"]: r for r in summary["report_by_model"]}
    wm = {r["model"]: r for r in summary["report_by_model_world_totals"]}
    sel, nav = bm["selected"], bm["seasonal_naive"]
    rep = summary["origins"]["report"]
    doc = SimpleDocTemplate(str(FRONT), pagesize=A4, rightMargin=12 * mm, leftMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm)
    s: list[Flowable] = []
    # ---- page 1: 해결방안 요약
    s += [P("롯데케미칼 [화학] 첨단소재 수요예측 AI 기획 — 예선 해결방안 요약", st["title"]), Spacer(1, 1.5 * mm),
          P("LOTTE AX Challenge 2026 · 지원자 전건우 · 본 3쪽은 과제 전용 요약이며, 이어지는 포트폴리오와 부록(증빙)은 기존 자료입니다.", st["meta"]), Spacer(1, 4 * mm)]
    s += [P("1. 문제 정의 — '주문 예측'이 아니라 'End Customer 수요 예측'", st["section"]), Spacer(1, 1.5 * mm)]
    s.append(labeled([
        ("현황", "첨단소재(ABS·PC)의 최종 수요처는 가전·TV·자동차·IT 완제품 제조사이지만 판매는 컴파운더·사출 협력사·유통사를 거치는 다단계 구조. 영업팀이 보는 것은 직접 고객의 PO이고, 최종 수요 변화는 1~2분기 늦게·증폭되어(bullwhip) 도착."),
        ("현행 한계", "① 담당자별 낙관·보수 편향이 측정되지 않음 ② grade×고객×지역 예측과 사업부 총량이 합산 불일치(one-number 부재) ③ 자동차 생산·가전 출하·역내 수입량·원료가·유가·환율 등 외부 선행 신호 미반영."),
        ("정의", "직접 고객 주문 이력 + 최종 제품 산업의 생산·출하·수입 지표를 선행 변수로 결합해 grade×고객군×지역×월 단위로 1~6개월 수요를 예측하고, 영업 FCST와 비교·조정해 확정 수요를 S&OP에 넘기는 월간 운영 체계. 모델 하나가 아니라 수집→예측→비교→설명→조정→확정→사후평가 워크플로우를 AI 에이전트가 운영."),
    ], st))
    s += [Spacer(1, 4 * mm), P("2. 해결 방안 — 4개 층", st["section"]), Spacer(1, 1.5 * mm)]
    s.append(grid([["층", "구성", "핵심 설계"],
                   ["데이터", "내부: 주문·출하·판가·재고·영업 FCST·고객 마스터(고객→최종산업·OEM 매핑)\n외부: 자동차 생산·생산지수·ABS/PC 수출입·중국 수입·원료가·유가·환율·이벤트", "월 단위 정렬, 공표 시차(publication lag) 강제 → '예측 시점에 알 수 있던 값'만 사용"],
                   ["예측 엔진", "계절 베이스라인 → LightGBM(외생변수, 직접 다중지평) → 시계열 파운데이션 모델(Chronos 제로샷) 앙상블", "롤링 백테스트 WAPE·bias로 계층별 모델 선택, MinT reconciliation으로 총량↔세부 합산 일치, P10/P90 예측 구간"],
                   ["AI 에이전트", "ingest·forecast·backtest·compare_fcst·detect_anomaly·explain·propose·record_decision·handoff 9개 도구를 LLM이 호출", "숫자는 도구만 생성, LLM은 계획·해석·설명. 조정안은 플래너 승인 전까지 '제안' 상태(human-in-the-loop)"],
                   ["운영", "S&OP 주기 월간 사이클, 정확도 KPI(WAPE·bias)와 재고 KPI 연결, 조정 이력·FVA 기록", "사람과 AI의 기여 분리 측정, 법인별 데이터 소스만 교체해 확산"]],
                  [18 * mm, 84 * mm, 84 * mm], st))
    s += [Spacer(1, 4 * mm), P("3. 기대 효과와 차별성", st["section"]), Spacer(1, 1.5 * mm)]
    s.append(labeled([
        ("기대 효과", "예측 오차 감소 → 안전재고·재고일수 감소, 긴급 생산변경·운송비 감소, 수요 전환기 판가 방어. 첨단소재 매출 약 5조원 기준 재고 회전 1일 단축 ≈ 운전자본 약 140억원(매출/365 단순 가정). 영업·생산·구매가 같은 숫자로 회의하는 one-number 체계, 해외 판매법인 확장."),
        ("차별성", "① 모델이 아닌 '담당자가 매달 쓰는 워크플로우'로 설계하고 FVA로 효과를 증명 ② End Customer 신호를 고객 마스터 매핑과 전방 산업 지표로 명시적으로 모델에 투입 ③ 예선에서 이미 공개 데이터로 파이프라인·백테스트·에이전트를 실제 구현해 검증(다음 쪽) ④ 본선에서는 내부 데이터 스키마 연결만 추가하면 되는 구조."),
    ], st))
    s += [Spacer(1, 4 * mm), P("4. 신뢰성·검증 설계", st["section"]), Spacer(1, 1.5 * mm)]
    s.append(grid([["항목", "설계"],
                   ["평가", "시간 순서를 지키는 롤링 오리진 백테스트. 지평별(1~6개월) WAPE·MASE·bias를 계층별로 보고하고, 베이스라인을 못 이기는 셀은 자동으로 베이스라인으로 회귀"],
                   ["누수 방지", "외부변수 공표 시차를 피처 생성 시점에 강제, 선택 창(12개월)과 보고 창(12개월) 분리로 모델 선택 편향 제거"],
                   ["불확실성", "P10/P90 예측 구간과 계층별 신뢰도 등급을 함께 제시해 담당자가 봐야 할 셀을 우선순위화"],
                   ["LLM 안전장치", "LLM은 숫자를 만들지 않고 도구 결과만 인용(근거 도구명 첨부). 조정안은 승인 전까지 '제안' 상태, 승인·수정·반려 이력 기록"],
                   ["운영", "데이터 품질 게이트 실패 시 사이클 중단, 드리프트 감시·재학습 트리거, 직전 확정 FCST로 롤백, 고정 시드·버전으로 재현"]],
                  [26 * mm, 160 * mm], st))
    s.append(PageBreak())
    # ---- page 2: 데모 결과
    s += [P("예선 데모 — 공개 데이터로 구현한 수요예측 파이프라인과 백테스트", st["title"]), Spacer(1, 1.5 * mm),
          P(f"출하 대리변수 = 한국의 ABS(HS 3903.30)·PC(HS 3907.40) 월별 수출량(UN Comtrade/관세청), 고객군 = 수출 대상국(상위 {summary['panel']['top_n_partners']}개국 + 기타). "
            f"패널 {summary['panel']['series']}개 시계열, {summary['panel']['first_month'][:7]}~{summary['panel']['last_month'][:7]}. 외부 변수: {', '.join(summary['exog'])}.", st["meta"]), Spacer(1, 3 * mm)]
    impr = (1 - sel["wape"] / nav["wape"]) * 100
    s.append(kpi_row([(f"{sel['wape']*100:.1f}%", "선택 모델 WAPE (전체 셀, h=1~6)"), (f"{nav['wape']*100:.1f}%", "계절 naive WAPE (기준선)"),
                      (f"{impr:.0f}%", "naive 대비 오차 감소"), (f"{wm['selected']['wape']*100:.1f}%", f"월드 총량 WAPE (naive {wm['seasonal_naive']['wape']*100:.1f}%)"),
                      (f"{sel['bias']*100:+.1f}%", "선택 모델 bias")], st))
    s += [Spacer(1, 2 * mm), P(f"보고 창: {rep[0][:7]}~{rep[-1][:7]} 12개 기준월(선택 창 12개월에서 계층별 모델을 고른 뒤, 보고 창에서만 채점해 선택 편향 제거). 지표는 WAPE=Σ|오차|/Σ|실적|, bias=Σ(예측-실적)/Σ|실적|.", st["small"]), Spacer(1, 3 * mm)]
    s.append(Table([[img(ART / "charts/wape_by_model.png", 91 * mm), img(ART / "charts/wape_by_horizon.png", 91 * mm)]], colWidths=[93 * mm, 93 * mm]))
    s.append(Spacer(1, 2 * mm))
    s.append(Table([[img(ART / "charts/series_ABS_WLD.png", 91 * mm), img(ART / "charts/series_PC_WLD.png", 91 * mm)]], colWidths=[93 * mm, 93 * mm]))
    s += [Spacer(1, 3 * mm), P("모델별 결과 (보고 창, 전체 셀)", st["section"]), Spacer(1, 1.5 * mm)]
    s.append(grid([["모델", "WAPE", "bias", "MASE", "비고"]] + [[r["model"], f"{r['wape']*100:.1f}%", f"{r['bias']*100:+.1f}%", f"{r.get('mase', float('nan')):.2f}",
                   {"seasonal_naive": "12개월 전 값", "ets": "AutoETS(statsforecast)", "lgbm": "LightGBM 글로벌·직접 다중지평·외생변수", "chronos": "Chronos-Bolt 제로샷", "ensemble": "ETS+LGBM+Chronos 평균", "selected": "계층별 선택(선택 창 기준)"}.get(r["model"], "")]
                   for r in summary["report_by_model"]], [28 * mm, 20 * mm, 20 * mm, 20 * mm, 98 * mm], st))
    bs = summary["report_by_series"]
    sel_rows = {r["unique_id"]: r for r in bs if r["model"] == "selected"}
    nav_rows = {r["unique_id"]: r for r in bs if r["model"] == "seasonal_naive"}
    series_tbl = [["시계열", "선택 모델 WAPE", "naive WAPE", "개선", "bias"]]
    for uid in summary["panel"]["labels"]:
        if uid in sel_rows and uid in nav_rows:
            a, b = sel_rows[uid]["wape"], nav_rows[uid]["wape"]
            series_tbl.append([summary["panel"]["labels"][uid], f"{a*100:.1f}%", f"{b*100:.1f}%", f"{(1-a/b)*100:+.0f}%", f"{sel_rows[uid]['bias']*100:+.1f}%"])
    s += [Spacer(1, 3 * mm), P("시계열별 결과 (보고 창, h=1~6)", st["section"]), Spacer(1, 1.5 * mm)]
    half = (len(series_tbl) - 1 + 1) // 2
    left, right = series_tbl[:half + 1], [series_tbl[0]] + series_tbl[half + 1:]
    w = [38 * mm, 16 * mm, 14 * mm, 11 * mm, 12 * mm]
    s.append(Table([[grid(left, w, st), grid(right, w, st)]], colWidths=[93 * mm, 93 * mm]))
    s += [Spacer(1, 3 * mm), P("선택된 모델 분포: " + " · ".join(f"{k} {v}" for k, v in summary["best_model_counts"].items()) + ". 모든 코드·데이터 스크립트는 재현 가능하며(uv 가상환경, 고정 시드), 본선에서는 Comtrade 로더를 내부 출하 데이터 로더로 교체합니다.", st["small"])]
    s.append(PageBreak())
    # ---- page 3: 에이전트
    s += [P("AI 에이전트 프로토타입 — 월간 사이클을 도구 호출로 운영", st["title"]), Spacer(1, 1.5 * mm),
          P("LLM(OpenAI/Anthropic tool-use)이 9개 결정론적 도구를 순서대로 호출하고 결과만 인용해 보고합니다. 키가 없는 환경에서는 같은 도구 순서를 스크립트로 실행하는 fallback을 둡니다.", st["meta"]), Spacer(1, 3 * mm)]
    agent_shot = SHOTS / "ui_agent.png"
    if agent_shot.exists():
        with Image.open(agent_shot) as im:
            crop = im.crop((60, 30, im.width - 60, min(im.height, 1250)))
            cropped = OUT / "ui_agent_crop.png"
            crop.save(cropped)
        s += [img(cropped, CONTENT_WIDTH, 150 * mm), Spacer(1, 1 * mm),
              P("데모 UI: 왼쪽은 도구 호출 기록(입력·출력 JSON), 오른쪽은 gpt-6.1-terra가 도구 결과만 인용해 작성한 월간 보고(기준월 2025-12, 도구 11회, 약 1분). 영업 FCST는 공개 데이터 데모용 합성값.", st["meta"]), Spacer(1, 3 * mm)]
    s += [P("도구와 사이클", st["section"]), Spacer(1, 1.5 * mm)]
    s.append(grid([["단계", "도구", "역할 / 안전장치"],
                   ["1 수집·점검", "ingest", "패널·외부변수 정렬, 결측·0 구간·공표 시차 점검, 품질 게이트 실패 시 사이클 중단"],
                   ["2 예측", "forecast", "앙상블+계층별 선택, MinT-OLS로 총량·세부 합산 일치, P10/P90 구간"],
                   ["3 정확도", "backtest_summary", "보고 창 WAPE·bias·MASE(모델·지평·시계열별)"],
                   ["4 비교", "compare_with_sales_fcst", "영업 FCST와 셀 단위 편차, 임계치 초과 셀 목록(데모는 합성 FCST)"],
                   ["5 이상감지", "detect_anomalies", "최근 실적의 잔차 z-score, 외부변수 3개월 변화 z-score"],
                   ["6 설명·제안", "explain / propose_adjustment", "LightGBM 기여도·최근 실적·YoY 근거, 백테스트 정확도로 AI 가중치 산정한 조정안(제안 상태)"],
                   ["7 확정", "record_decision / handoff", "플래너 결정(수락·유지·직접 입력)만 기록, 확정 FCST를 S&OP 파일로 출력, 익월 FVA 산출"]],
                  [22 * mm, 42 * mm, 122 * mm], st))
    s += [Spacer(1, 3 * mm), P("본선 계획", st["section"]), Spacer(1, 1.5 * mm),
          P("① 내부 주문·출하·영업 FCST 스키마 매핑과 고객 마스터(최종 산업·OEM) 연결 ② grade×고객 계층으로 패널 재구성, 동일 백테스트로 정확도 재측정 ③ 영업 담당자 워크플로우(비교·설명·조정·확정) UI 고도화와 FVA 리포트 ④ 드리프트 감시·재학습 트리거·롤백 등 운영 안정화.", st["small"])]
    doc.build(s, onFirstPage=page_number, onLaterPages=page_number)


def build_appendix() -> None:
    st = styles()
    doc = SimpleDocTemplate(str(APPX), pagesize=A4, rightMargin=12 * mm, leftMargin=12 * mm, topMargin=12 * mm, bottomMargin=14 * mm)
    s: list[Flowable] = [P("부록 — 수상 증빙", st["title"]), Spacer(1, 1.5 * mm),
         P("2020.08.12 한국멀티미디어학회 우수논문상 · 2019.11.08 한국인터넷방송통신학회 우수논문상 · 2018.11.28 공주대 컴퓨터공학부 학술제 대상", st["meta"]), Spacer(1, 4 * mm)]
    import subprocess, tempfile
    tmp = Path(tempfile.mkdtemp())
    imgs = []
    for i, name in enumerate(["1. 우수논문상(IIBC2019).pdf", "2. 우수논문상(한국멀티미디어학회).pdf"]):
        subprocess.run(["pdftoppm", "-png", "-r", "70", "-f", "1", "-l", "1", str(AWARDS / name), str(tmp / f"a{i}")], check=True)
        imgs.append(next(tmp.glob(f"a{i}*.png")))
    imgs.append(AWARDS / "캡스톤 대상.jpg")
    s.append(Table([[img(p, 58 * mm, 120 * mm) for p in imgs]], colWidths=[62 * mm] * 3))
    doc.build(s, onFirstPage=appendix_page_number, onLaterPages=appendix_page_number)


def merge() -> None:
    w = PdfWriter()
    for path in (FRONT, EXISTING, APPX):
        for page in PdfReader(str(path)).pages:
            w.add_page(page)
    with FINAL.open("wb") as f:
        w.write(f)
    print(FINAL, f"{FINAL.stat().st_size/1e6:.1f} MB", len(PdfReader(str(FINAL)).pages), "pages")


if __name__ == "__main__":
    summary = json.loads((ART / "summary.json").read_text())
    cycle_path = ART / "cycle_for_portfolio.json"
    cycle = json.loads(cycle_path.read_text()) if cycle_path.exists() else None
    build_front(summary, cycle)
    build_appendix()
    merge()
