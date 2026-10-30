"""FastAPI surface for the demand-forecasting agent demo (local only)."""
from __future__ import annotations

import json
import sys
from pathlib import Path

from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from pydantic import BaseModel

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT.parent / ".env")
from lotte_demand.agent.runner import run_cycle  # noqa: E402
from lotte_demand.agent.tools import Workspace  # noqa: E402

ART = ROOT / "artifacts"
app = FastAPI(title="LOTTE ABS/PC demand agent demo")
_ws: Workspace | None = None


def ws() -> Workspace:
    global _ws
    if _ws is None:
        _ws = Workspace.load(ART)
    return _ws


@app.get("/", response_class=HTMLResponse)
def index():
    return (Path(__file__).with_name("index.html")).read_text()


@app.get("/api/summary")
def summary():
    return json.loads((ART / "summary.json").read_text())


@app.get("/api/forecast")
def forecast(origin: str | None = None):
    return ws().forecast(origin)


@app.get("/api/compare")
def compare(origin: str | None = None, threshold_pct: float = 15.0):
    return ws().compare_with_sales_fcst(origin, threshold_pct)


@app.get("/api/anomalies")
def anomalies(origin: str | None = None):
    return ws().detect_anomalies(origin)


@app.get("/api/explain")
def explain(unique_id: str, h: int = 1):
    return ws().explain(unique_id, h)


@app.get("/api/propose")
def propose(unique_id: str, h: int):
    return ws().propose_adjustment(unique_id, h)


class Decision(BaseModel):
    unique_id: str
    h: int
    decision: str
    value_t: float | None = None
    note: str = ""


@app.post("/api/decision")
def decision(d: Decision):
    return ws().record_decision(d.unique_id, d.h, d.decision, d.value_t, d.note)


@app.get("/api/decisions")
def decisions():
    p = ART / "decisions.jsonl"
    return [json.loads(l) for l in p.read_text().splitlines()] if p.exists() else []


@app.post("/api/handoff")
def handoff():
    return ws().handoff()


class CycleReq(BaseModel):
    backend: str = "auto"
    origin: str | None = None
    planner_input: str | None = None


@app.post("/api/cycle")
def cycle(req: CycleReq):
    out = run_cycle(ws(), backend=req.backend, origin=req.origin, planner_input=req.planner_input, verbose=False)
    return JSONResponse(json.loads(json.dumps(out, default=str)))


@app.get("/charts/{name}")
def chart(name: str):
    p = ART / "charts" / name
    if not p.exists() or p.suffix != ".png":
        raise HTTPException(404)
    return FileResponse(p)
