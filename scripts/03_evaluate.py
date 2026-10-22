#!/usr/bin/env python3
"""Phase 3: evaluate the LangGraph triage pipeline on the test split.

Writes:
  results/eval_<model>.json            — full-system custom metrics
  results/ablation_graph_tools.json    — full system vs. no-graph-tools
  results/ablation_retrieval_modes.json — each retrieval mode (reuses the
                                           full system's diagnosis; only
                                           the retriever+writer tail reruns)
  results/eval_raw_<model>.jsonl       — per-incident scores (incremental,
                                           so a long run is resumable/debuggable)
  results/ragas_input.jsonl            — question/contexts/answer/reference
                                           rows per (incident, mode), consumed
                                           by scripts/03b_ragas_eval.py which
                                           runs in the isolated .venv-eval
                                           (see requirements-eval.txt)

Usage: uv run --extra dev scripts/03_evaluate.py [--limit N]
"""

from __future__ import annotations

import argparse
import json
import os
import random
import sys
import time
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from noc_copilot.agents.graph import build_triage_graph  # noqa: E402
from noc_copilot.agents.pipeline import finalize_result, invoke_graph  # noqa: E402
from noc_copilot.agents.remediation_writer import remediation_writer  # noqa: E402
from noc_copilot.agents.runbook_retriever import make_runbook_retriever_node  # noqa: E402
from noc_copilot.eval.retrieval_eval import load_bundles  # noqa: E402
from noc_copilot.eval.triage_eval import aggregate_scores, score_incident  # noqa: E402
from noc_copilot.graph.loader import get_driver  # noqa: E402
from noc_copilot.retrieval.chunking import load_runbook_chunks  # noqa: E402
from noc_copilot.retrieval.hybrid import build_retriever  # noqa: E402


def model_slug() -> str:
    return os.environ.get("LLM_MODEL", "unknown-model").replace("/", "-")


def _build_reference_lookup(runbooks_dir: Path) -> dict[str, str]:
    """runbook_id -> concatenated section text, used as RAGAS's "reference"
    (ground truth) for context_recall/context_precision — see
    scripts/03b_ragas_eval.py (runs in the isolated .venv-eval)."""
    by_runbook: dict[str, list[str]] = {}
    for chunk in load_runbook_chunks(runbooks_dir):
        by_runbook.setdefault(chunk.runbook_id, []).append(chunk.text)
    return {rid: "\n\n".join(texts) for rid, texts in by_runbook.items()}


def _extract_query(result: dict) -> str:
    return next((t["query"] for t in result["trace"] if t.get("node") == "runbook_retriever"), "")


def _ragas_row(incident_id: str, mode: str, result: dict, reference: str) -> dict:
    report = result.get("report") or {}
    answer = (
        report.get("incident_summary", "") + " " + " ".join(report.get("remediation_steps", []))
    )
    return {
        "incident_id": incident_id,
        "mode": mode,
        "question": _extract_query(result),
        "contexts": [c["text"] for c in result.get("retrieved_chunks", [])],
        "answer": answer.strip(),
        "reference": reference,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--limit", type=int, default=None, help="override configs/eval.yaml sample_size"
    )
    parser.add_argument(
        "--output-name",
        default=None,
        help=(
            "override the model-slug-derived results filename, e.g. "
            "--output-name base_small -> results/eval_base_small.json "
            "(used by Phase 4's base-vs-finetuned comparison, see SPEC.md 7.3)"
        ),
    )
    args = parser.parse_args()

    load_dotenv(REPO_ROOT / ".env")
    eval_cfg = yaml.safe_load((REPO_ROOT / "configs" / "eval.yaml").read_text())
    retrieval_cfg = yaml.safe_load((REPO_ROOT / "configs" / "retrieval.yaml").read_text())
    results_dir = REPO_ROOT / "results"
    results_dir.mkdir(parents=True, exist_ok=True)

    sample_size = args.limit if args.limit is not None else eval_cfg["sample_size"]
    default_mode = eval_cfg["default_retrieval_mode"]
    max_loops = eval_cfg["max_diagnostic_loops"]
    ablation_modes = [m for m in eval_cfg["ablation_retrieval_modes"] if m != default_mode]

    bundles = load_bundles(REPO_ROOT / "data" / "incidents" / "test.jsonl")
    if sample_size is not None:
        rng = random.Random(eval_cfg["seed"])
        bundles = rng.sample(bundles, k=min(sample_size, len(bundles)))
    output_name = args.output_name or model_slug()
    print(f"Evaluating on {len(bundles)} test-split incidents (model={model_slug()})")

    driver = get_driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ["NEO4J_PASSWORD"],
    )
    retriever = build_retriever(retrieval_cfg, REPO_ROOT / "data" / "runbooks", REPO_ROOT)
    top_k = retrieval_cfg["top_k"]
    graph = build_triage_graph(driver, retriever, retrieval_top_k=top_k)
    retriever_node_fn = make_runbook_retriever_node(retriever, top_k)
    reference_by_runbook_id = _build_reference_lookup(REPO_ROOT / "data" / "runbooks")

    raw_path = results_dir / f"eval_raw_{model_slug()}.jsonl"
    ragas_input_path = results_dir / "ragas_input.jsonl"
    full_scores: list[dict] = []
    no_tools_scores: list[dict] = []
    retrieval_mode_scores: dict[str, list[dict]] = {
        m: [] for m in eval_cfg["ablation_retrieval_modes"]
    }

    with open(raw_path, "w") as raw_f, open(ragas_input_path, "w") as ragas_f:
        for i, bundle in enumerate(bundles):
            alarm_dicts = [a.model_dump() for a in bundle.alarms]
            t0 = time.perf_counter()
            try:
                # --- full system ---
                full_state, full_latency = invoke_graph(
                    graph,
                    alarm_dicts,
                    use_graph_tools=True,
                    retrieval_mode=default_mode,
                    max_diagnostic_loops=max_loops,
                )
                full_result = finalize_result(full_state, full_latency)
                full_score = score_incident(driver, bundle, full_result)
                full_scores.append(full_score)
                retrieval_mode_scores[default_mode].append(full_score)
                reference = reference_by_runbook_id.get(bundle.runbook_id, "")
                ragas_f.write(
                    json.dumps(_ragas_row(bundle.incident_id, default_mode, full_result, reference))
                    + "\n"
                )

                # --- ablation: no graph tools ---
                no_tools_state, no_tools_latency = invoke_graph(
                    graph,
                    alarm_dicts,
                    use_graph_tools=False,
                    retrieval_mode=default_mode,
                    max_diagnostic_loops=max_loops,
                )
                no_tools_result = finalize_result(no_tools_state, no_tools_latency)
                no_tools_score = score_incident(driver, bundle, no_tools_result)
                no_tools_scores.append(no_tools_score)

                # --- ablation: other retrieval modes (reuse the full
                # system's own correlated alarms/diagnosis; only the
                # retriever+writer tail reruns, not the whole pipeline) ---
                for mode in ablation_modes:
                    mode_state = {
                        "correlated_alarms": full_state.get("correlated_alarms", []),
                        "correlated_node_ids": full_state.get("correlated_node_ids", []),
                        "hypotheses": full_state.get("hypotheses", []),
                        "confidence": full_state.get("confidence", 0.0),
                        "retrieval_mode": mode,
                        "trace": [],
                    }
                    t_mode = time.perf_counter()
                    # calling these node functions directly (not via
                    # graph.invoke) bypasses langgraph's trace-accumulating
                    # reducer, so merge "trace" by hand instead of
                    # dict.update() clobbering it
                    for update in (retriever_node_fn(mode_state), remediation_writer(mode_state)):
                        trace = mode_state["trace"] + update.pop("trace", [])
                        mode_state.update(update)
                        mode_state["trace"] = trace
                    mode_latency = time.perf_counter() - t_mode
                    mode_result = finalize_result(mode_state, mode_latency)
                    mode_score = score_incident(driver, bundle, mode_result)
                    retrieval_mode_scores[mode].append(mode_score)
                    ragas_f.write(
                        json.dumps(_ragas_row(bundle.incident_id, mode, mode_result, reference))
                        + "\n"
                    )

                ragas_f.flush()
                raw_f.write(
                    json.dumps(
                        {
                            "incident_id": bundle.incident_id,
                            "full_system": full_score,
                            "no_graph_tools": no_tools_score,
                        }
                    )
                    + "\n"
                )
                raw_f.flush()
            except Exception as e:
                print(f"  [{i + 1}/{len(bundles)}] FAILED {bundle.incident_id}: {e}")
                continue

            if (i + 1) % 10 == 0 or i == len(bundles) - 1:
                elapsed = time.perf_counter() - t0
                progress = f"[{i + 1}/{len(bundles)}] {bundle.failure_type.value}"
                print(f"  {progress} ({elapsed:.1f}s/incident)")

    driver.close()

    pricing = eval_cfg["pricing"]
    main_eval = aggregate_scores(
        full_scores, pricing["input_cost_per_1m_usd"], pricing["output_cost_per_1m_usd"]
    )
    main_eval["model"] = model_slug()
    main_eval["pricing_note"] = (
        f"mean_hypothetical_cost_per_incident_usd uses {pricing['model']}'s published "
        "paid-tier rate from configs/eval.yaml, applied to this run's token counts — not "
        "necessarily the rate for the model actually evaluated here (model=" + model_slug() + "). "
        "For a self-hosted model, Phase 5's GPU-hourly cost estimate is the meaningful figure; "
        "actual API spend for this run, if applicable, was $0 (free tier)."
    )
    (results_dir / f"eval_{output_name}.json").write_text(json.dumps(main_eval, indent=2))

    graph_tools_ablation = {
        "full_system": aggregate_scores(full_scores),
        "no_graph_tools": aggregate_scores(no_tools_scores),
    }
    (results_dir / "ablation_graph_tools.json").write_text(
        json.dumps(graph_tools_ablation, indent=2)
    )

    retrieval_ablation = {
        mode: aggregate_scores(scores) for mode, scores in retrieval_mode_scores.items()
    }
    (results_dir / "ablation_retrieval_modes.json").write_text(
        json.dumps(retrieval_ablation, indent=2)
    )

    print(f"\nMain eval -> results/eval_{output_name}.json")
    print(json.dumps(main_eval, indent=2))
    print("\nGraph-tools ablation -> results/ablation_graph_tools.json")
    print(json.dumps(graph_tools_ablation, indent=2))
    print("\nRetrieval-mode ablation -> results/ablation_retrieval_modes.json")
    print(json.dumps(retrieval_ablation, indent=2))


if __name__ == "__main__":
    main()
