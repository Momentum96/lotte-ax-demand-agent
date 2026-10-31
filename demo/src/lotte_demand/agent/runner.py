"""Monthly-cycle agent runner.

The LLM plans and explains; every number comes from a tool result. Three backends:
  - "openai"    : Chat Completions function calling (OPENAI_API_KEY, OPENAI_MODEL)
  - "anthropic" : Messages API tool use (ANTHROPIC_API_KEY, ANTHROPIC_MODEL)
  - "scripted"  : fixed tool sequence + templated Korean summary (no key needed)
"""
from __future__ import annotations

from typing import Any

import json
import os
import time
from pathlib import Path

from .tools import TOOL_SPECS, Workspace, dispatch

SYSTEM_PROMPT = """당신은 롯데케미칼 첨단소재(ABS·PC) 수요예측 운영 에이전트입니다. 영업·SCM 담당자(플래너)를 위해 월간 사이클을 수행합니다.

반드시 지킬 규칙:
1. 숫자는 도구 결과에서만 인용합니다. 스스로 계산하거나 추정한 숫자를 쓰지 않습니다.
2. 사이클 순서: ingest → forecast → backtest_summary → compare_with_sales_fcst → detect_anomalies → 편차가 큰 셀 최대 3개에 대해 explain과 propose_adjustment → 플래너 확인 요청.
3. record_decision과 handoff는 플래너의 명시적 결정이 입력으로 주어진 경우에만 호출합니다. 그렇지 않으면 "제안" 상태로 멈추고 확인을 요청합니다.
4. 최종 보고는 한국어로, 다음 구조를 지킵니다: (1) 데이터 상태 (2) 이번 달 예측 핵심(월드 총량과 상위 변동 셀) (3) 영업 FCST 대비 편차 셀과 제안 (4) 이상 징후와 외부 변수 (5) 플래너가 결정해야 할 항목 목록. 데모에서 sales_fcst가 합성(synthetic)이라는 점은 한 줄로 명시합니다.
5. 단위는 톤(t), 비율은 %로 표기하고, 근거가 된 도구 이름을 괄호로 붙입니다."""


def _openai_tools():
    """Responses API function-tool format."""
    return [{"type": "function", "name": t["name"], "description": t["description"], "parameters": t["input_schema"], "strict": False} for t in TOOL_SPECS]


def _save(transcript: list[dict[str, Any]], ws: Workspace, origin: str, backend: str) -> Path:
    d = ws.artifacts / "transcripts"
    d.mkdir(exist_ok=True)
    path = d / f"cycle_{origin}_{backend}_{time.strftime('%Y%m%d-%H%M%S')}.json"
    path.write_text(json.dumps(transcript, ensure_ascii=False, indent=2, default=str))
    return path


def run_cycle(ws: Workspace, backend: str = "auto", origin: str | None = None, planner_input: str | None = None,
              max_steps: int = 24, verbose: bool = True, decisions: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    if backend == "auto":
        backend = "openai" if os.getenv("OPENAI_API_KEY") else "anthropic" if os.getenv("ANTHROPIC_API_KEY") else "scripted"
    origin_label = (origin or str(ws.panel.long["ds"].max().date()))[:7]
    user_msg = f"기준월(origin): {origin or '최신'}. 월간 사이클을 수행하고 최종 보고를 작성하세요."
    if planner_input:
        user_msg += f"\n\n플래너 결정 입력: {planner_input}\n이 결정들을 record_decision으로 기록한 뒤 handoff를 호출하고 결과를 보고에 포함하세요."
    transcript: list[dict[str, Any]] = [{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": user_msg}]
    t0 = time.time()
    if backend == "scripted":
        final = _scripted(ws, origin, transcript, verbose, decisions)
    elif backend == "openai":
        final = _openai_loop(ws, transcript, max_steps, verbose)
    elif backend == "anthropic":
        final = _anthropic_loop(ws, transcript, max_steps, verbose)
    else:
        raise ValueError(backend)
    path = _save(transcript, ws, origin_label, backend)
    n_tools = sum(1 for t in transcript if t.get("role") == "tool")
    return {"backend": backend, "origin": origin_label, "final": final, "n_tool_calls": n_tools,
            "elapsed_sec": round(time.time() - t0, 1), "transcript_path": str(path), "transcript": transcript}


def _call_tool(ws: Workspace, name: str, args: dict[str, Any], transcript: list[dict[str, Any]], verbose: bool) -> dict[str, Any]:
    t0 = time.time()
    try:
        result = dispatch(ws, name, args)
    except Exception as e:  # surface tool errors to the model instead of crashing the cycle
        result = {"error": f"{type(e).__name__}: {e}"}
    transcript.append({"role": "tool", "name": name, "args": args, "result": result, "sec": round(time.time() - t0, 1)})
    if verbose:
        print(f"  [tool] {name}({json.dumps(args, ensure_ascii=False)}) -> {str(result)[:120]}... ({time.time()-t0:.1f}s)", flush=True)
    return result


def _openai_loop(ws, transcript, max_steps, verbose):
    from openai import OpenAI
    client = OpenAI()
    model = os.getenv("OPENAI_MODEL", "gpt-6.1-terra")
    effort = os.getenv("OPENAI_REASONING_EFFORT", "medium")
    # Responses API: the running context is the list of input items; reasoning items returned by the
    # model are passed back verbatim so reasoning models keep their chain across tool calls.
    items: list[dict[str, Any]] = [{"role": "user", "content": transcript[1]["content"]}]
    for _ in range(max_steps):
        resp = client.responses.create(model=model, instructions=SYSTEM_PROMPT, input=items, tools=_openai_tools(),
                                       reasoning={"effort": effort})
        calls = [it for it in resp.output if it.type == "function_call"]
        text = resp.output_text or ""
        if text:
            transcript.append({"role": "assistant", "content": text})
        if not calls:
            return text
        items.extend(it.model_dump(exclude_none=True) for it in resp.output)
        for call in calls:
            args = json.loads(call.arguments or "{}")
            result = _call_tool(ws, call.name, args, transcript, verbose)
            items.append({"type": "function_call_output", "call_id": call.call_id,
                          "output": json.dumps(result, ensure_ascii=False, default=str)[:60000]})
    return "(max steps reached)"


def _anthropic_loop(ws, transcript, max_steps, verbose):
    import anthropic
    client = anthropic.Anthropic()
    model = os.getenv("ANTHROPIC_MODEL", "claude-sonnet-4-5")
    messages = [{"role": "user", "content": transcript[1]["content"]}]
    for _ in range(max_steps):
        resp = client.messages.create(model=model, max_tokens=4000, system=SYSTEM_PROMPT, tools=TOOL_SPECS, messages=messages)
        messages.append({"role": "assistant", "content": resp.content})
        text = "".join(b.text for b in resp.content if b.type == "text")
        if text:
            transcript.append({"role": "assistant", "content": text})
        uses = [b for b in resp.content if b.type == "tool_use"]
        if not uses or resp.stop_reason == "end_turn":
            return text
        results = []
        for b in uses:
            result = _call_tool(ws, b.name, b.input or {}, transcript, verbose)
            results.append({"type": "tool_result", "tool_use_id": b.id, "content": json.dumps(result, ensure_ascii=False, default=str)[:60000]})
        messages.append({"role": "user", "content": results})
    return "(max steps reached)"


def _scripted(ws, origin, transcript, verbose, decisions=None):
    args = {"origin": origin} if origin else {}
    ing = _call_tool(ws, "ingest", args, transcript, verbose)
    fc = _call_tool(ws, "forecast", args, transcript, verbose)
    bt = _call_tool(ws, "backtest_summary", {}, transcript, verbose)
    cmp_ = _call_tool(ws, "compare_with_sales_fcst", args, transcript, verbose)
    an = _call_tool(ws, "detect_anomalies", args, transcript, verbose)
    proposals = []
    for cell in cmp_["flagged"][:3]:
        _call_tool(ws, "explain", {"unique_id": cell["unique_id"], "h": cell["h"]}, transcript, verbose)
        proposals.append(_call_tool(ws, "propose_adjustment", {"unique_id": cell["unique_id"], "h": cell["h"]}, transcript, verbose))
    sel = next((m for m in bt["by_model"] if m["model"] == "selected"), None)
    nav = next((m for m in bt["by_model"] if m["model"] == "seasonal_naive"), None)
    world = [r for r in fc["rows"] if r["unique_id"].endswith("|WLD") and r["h"] == 1]
    lines = [f"## 월간 수요예측 사이클 보고 (기준월 {fc['origin']}, scripted 모드)", "",
             f"**(1) 데이터 상태** (ingest): {ing['series']}개 시계열, {ing['first_month']}~{ing['last_actual_month']}, 품질 게이트 {ing['quality_gate']}, 외부 변수 {len(ing['drivers'])}개, 공표 시차 {ing['publication_lag_months']}개월.", "",
             "**(2) 이번 달 예측 핵심** (forecast, MinT-OLS 합산 일치):"]
    for w in world:
        lines.append(f"- {w['label']} {w['target_month']}: {w['forecast_t']:,.0f} t (P10 {w['p10_t']:,.0f} / P90 {w['p90_t']:,.0f}, 모델 {w['model']})")
    if sel and nav:
        lines += ["", f"**백테스트** (backtest_summary, 보고 창 {bt['window']['first_origin']}~{bt['window']['last_origin']}): selected WAPE {sel['wape']*100:.1f}% vs 계절 naive {nav['wape']*100:.1f}%, bias {sel['bias']*100:+.1f}%."]
    lines += ["", f"**(3) 영업 FCST 대비 편차 셀** (compare_with_sales_fcst, 임계 {cmp_['threshold_pct']}%): {cmp_['n_flagged']}/{cmp_['n_cells']}개 셀. ※ 데모의 sales_fcst는 합성값입니다."]
    for p in proposals:
        lines.append(f"- {p['label']} {p['target_month']}(h={p['h']}): AI {p['ai_forecast_t']:,.0f} t vs 영업 {p['sales_fcst_t']:,.0f} t → 제안 {p['proposed_t']:,.0f} t (AI 가중 {p['weight_on_ai']}, 백테스트 WAPE {p['ai_backtest_wape']}) [propose_adjustment]")
    lines += ["", "**(4) 이상 징후** (detect_anomalies):"]
    for s in [s for s in an["series_surprises"] if s["flag"]][:5]:
        lines.append(f"- {s['label']}: 실적 {s['actual_t']:,.0f} t vs 기대 {s['expected_t']:,.0f} t (z={s['z']})")
    for d in [d for d in an["driver_shifts"] if d["flag"]][:5]:
        lines.append(f"- 외부 변수 {d['driver']}: 3개월 변화 {d['chg_3m_pct']:+.1f}% (z={d['z']})")
    if not any(s["flag"] for s in an["series_surprises"]) and not any(d["flag"] for d in an["driver_shifts"]):
        lines.append("- 임계치를 넘는 이상 징후 없음")
    lines += ["", "**(5) 플래너 결정 필요 항목**:"]
    for p in proposals:
        lines.append(f"- [ ] {p['unique_id']} h={p['h']}: 제안 {p['proposed_t']:,.0f} t 수락 / AI {p['ai_forecast_t']:,.0f} t 유지 / 직접 입력")
    if decisions:  # explicit planner decisions were given: record them and hand off (system prompt rule 3)
        lines += ["", "**플래너 결정 반영** (record_decision → handoff):"]
        for d in decisions:
            rec = _call_tool(ws, "record_decision", {**d, **args}, transcript, verbose)
            lines.append(f"- {d['unique_id']} h={d['h']}: {d['decision']}" + (f" → 오류: {rec['error']}" if "error" in rec else ""))
        ho = _call_tool(ws, "handoff", args, transcript, verbose)
        if "error" in ho:
            lines.append(f"- handoff 오류: {ho['error']}")
        else:
            lines.append(f"- 확정 FCST {ho['file']}: 결정 반영 {ho['n_overridden']}셀, 합산 일치용 재조정 {ho['n_reconciled']}셀")
    final = "\n".join(lines)
    transcript.append({"role": "assistant", "content": final})
    return final
