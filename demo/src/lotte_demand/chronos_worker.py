"""Persistent Chronos-Bolt worker. Runs in its own process so torch's OpenMP runtime never shares a
process with LightGBM/numba (macOS libomp conflict). Protocol: one JSON object per line on stdin
{"ctx": [[...], ...], "H": 6, "q": [0.1, 0.5, 0.9]} -> one JSON line on stdout {"quantiles": [[[...]...]...]}.
"""
import json
import os
import sys

os.environ.setdefault("TOKENIZERS_PARALLELISM", "false")
os.environ.setdefault("HF_HUB_OFFLINE", "1")


def main() -> None:
    import torch
    from chronos import BaseChronosPipeline
    model_id = sys.argv[1] if len(sys.argv) > 1 else "amazon/chronos-bolt-small"
    try:
        pipe = BaseChronosPipeline.from_pretrained(model_id, device_map="cpu", torch_dtype=torch.float32)
    except Exception:  # first run may need the download
        os.environ["HF_HUB_OFFLINE"] = "0"
        pipe = BaseChronosPipeline.from_pretrained(model_id, device_map="cpu", torch_dtype=torch.float32)
    sys.stdout.write(json.dumps({"ready": True}) + "\n"); sys.stdout.flush()
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        req = json.loads(line)
        if req.get("op") == "quit":
            break
        ctx = [torch.tensor(c, dtype=torch.float32) for c in req["ctx"]]
        q, _ = pipe.predict_quantiles(ctx, prediction_length=int(req["H"]), quantile_levels=req.get("q", [0.1, 0.5, 0.9]))
        sys.stdout.write(json.dumps({"quantiles": q.numpy().tolist()}) + "\n"); sys.stdout.flush()


if __name__ == "__main__":
    main()
