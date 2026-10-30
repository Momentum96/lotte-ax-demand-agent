"""Run one monthly agent cycle. Usage:
  python scripts/run_cycle.py [--backend auto|openai|anthropic|scripted] [--origin YYYY-MM-01] [--decisions "ABS|CHN:1:accept_proposed;PC|WLD:2:override:5000"]
"""
import argparse
import sys
from pathlib import Path

from dotenv import load_dotenv

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
load_dotenv(ROOT.parent / ".env")
from lotte_demand.agent.runner import run_cycle  # noqa: E402
from lotte_demand.agent.tools import Workspace  # noqa: E402


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--backend", default="auto")
    ap.add_argument("--origin", default=None)
    ap.add_argument("--decisions", default=None, help="planner decisions, e.g. 'ABS|CHN:1:accept_proposed;PC|WLD:2:override:5000'")
    a = ap.parse_args()
    ws = Workspace.load(ROOT / "artifacts")
    planner = None
    if a.decisions:
        items = []
        for part in a.decisions.split(";"):
            f = part.split(":")
            items.append(f"{f[0]} h={f[1]} -> {f[2]}" + (f" value_t={f[3]}" if len(f) > 3 else ""))
        planner = "; ".join(items)
    out = run_cycle(ws, backend=a.backend, origin=a.origin, planner_input=planner)
    print("\n" + "=" * 80)
    print(out["final"])
    print("=" * 80)
    print(f"backend={out['backend']} tool_calls={out['n_tool_calls']} elapsed={out['elapsed_sec']}s transcript={out['transcript_path']}")
    ws.chronos.close()


if __name__ == "__main__":
    main()
