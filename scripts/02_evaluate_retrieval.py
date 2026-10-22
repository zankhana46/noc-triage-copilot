#!/usr/bin/env python3
"""Phase 2: evaluate recall@k / MRR for each retrieval mode (bm25, dense,
hybrid, hybrid+rerank) on queries built from the test-split incident
bundles. Writes results/retrieval_eval.json.

Usage: uv run --extra dev scripts/02_evaluate_retrieval.py
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from noc_copilot.eval.retrieval_eval import evaluate_mode, load_bundles  # noqa: E402
from noc_copilot.retrieval.hybrid import build_retriever  # noqa: E402


def main() -> None:
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "retrieval.yaml").read_text())
    test_path = REPO_ROOT / "data" / "incidents" / "test.jsonl"
    if not test_path.exists():
        raise SystemExit(
            "data/incidents/test.jsonl not found; run scripts/01_generate_data.py first"
        )

    print("Building retrieval indices...")
    retriever = build_retriever(cfg, REPO_ROOT / "data" / "runbooks", REPO_ROOT)
    print(f"  indexed {len(retriever.chunks)} chunks from data/runbooks/")

    bundles = load_bundles(test_path)
    print(f"Evaluating on {len(bundles)} test-split incidents...")

    results = {}
    for mode in cfg["modes"]:
        metrics = evaluate_mode(retriever, bundles, mode)
        results[mode] = metrics
        print(f"  [{mode:14s}] " + ", ".join(f"{k}={v}" for k, v in metrics.items() if k != "mode"))

    out_path = REPO_ROOT / "results" / "retrieval_eval.json"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(results, indent=2))
    print(f"\nWritten to {out_path}")


if __name__ == "__main__":
    main()
