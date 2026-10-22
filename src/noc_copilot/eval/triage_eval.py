"""Phase 3 custom ground-truth metrics: RCA type accuracy, root-node
localization, affected-node F1, and latency — computed against the
hidden ground truth in each incident bundle (SPEC.md 6.3).
"""

from __future__ import annotations

from neo4j import Driver

from noc_copilot.graph.tools import are_adjacent
from noc_copilot.simulator.schema import IncidentBundle


def affected_node_f1(predicted: list[str], ground_truth: list[str]) -> dict[str, float]:
    pred_set, true_set = set(predicted), set(ground_truth)
    if not pred_set and not true_set:
        return {"precision": 1.0, "recall": 1.0, "f1": 1.0}
    if not pred_set or not true_set:
        return {"precision": 0.0, "recall": 0.0, "f1": 0.0}
    overlap = len(pred_set & true_set)
    precision = overlap / len(pred_set)
    recall = overlap / len(true_set)
    f1 = 2 * precision * recall / (precision + recall) if (precision + recall) else 0.0
    return {"precision": precision, "recall": recall, "f1": f1}


def score_incident(driver: Driver, bundle: IncidentBundle, result: dict) -> dict:
    """Per-incident scoring of one triage result against its bundle's
    hidden ground truth. `result` is run_triage()'s return dict."""
    report = result.get("report") or {}
    hypotheses = report.get("top3_hypotheses", [])
    predicted_node = report.get("root_cause_node")
    predicted_type = report.get("root_cause_type")

    top1_type_correct = predicted_type == bundle.failure_type.value
    top3_type_correct = bundle.failure_type.value in {h["root_cause_type"] for h in hypotheses}

    node_exact_match = predicted_node == bundle.root_cause_node
    node_within_one_hop = bool(
        predicted_node and are_adjacent(driver, predicted_node, bundle.root_cause_node)
    )

    f1_scores = affected_node_f1(report.get("affected_nodes", []), bundle.affected_nodes)

    return {
        "incident_id": bundle.incident_id,
        "failure_type": bundle.failure_type.value,
        "top1_type_correct": top1_type_correct,
        "top3_type_correct": top3_type_correct,
        "node_exact_match": node_exact_match,
        "node_within_one_hop": node_within_one_hop,
        "affected_node_precision": f1_scores["precision"],
        "affected_node_recall": f1_scores["recall"],
        "affected_node_f1": f1_scores["f1"],
        "latency_seconds": result["latency_seconds"],
        "prompt_tokens": result["total_prompt_tokens"],
        "completion_tokens": result["total_completion_tokens"],
        "total_tokens": result["total_tokens"],
        "num_diagnostic_loops": result["num_diagnostic_loops"],
        "confidence": report.get("confidence", 0.0),
    }


def _percentile(values: list[float], pct: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    idx = min(len(ordered) - 1, max(0, round(pct * (len(ordered) - 1))))
    return ordered[idx]


def aggregate_scores(
    per_incident: list[dict],
    input_cost_per_1m_usd: float = 0.0,
    output_cost_per_1m_usd: float = 0.0,
) -> dict:
    """input_cost_per_1m_usd / output_cost_per_1m_usd: the model's actual
    published paid-tier rate (see results/eval_<model>.json's "pricing_note"
    for the source) — used only to report a hypothetical cost-per-incident
    figure for the Phase 4/5 API-vs-self-hosted comparison. Actual spend on
    the free tier used for this run is $0."""
    n = len(per_incident)
    if n == 0:
        return {"n_incidents": 0}
    latencies = [r["latency_seconds"] for r in per_incident]
    prompt_tokens = [r["prompt_tokens"] for r in per_incident]
    completion_tokens = [r["completion_tokens"] for r in per_incident]
    mean_cost = (
        (sum(prompt_tokens) / n / 1_000_000) * input_cost_per_1m_usd
        + (sum(completion_tokens) / n / 1_000_000) * output_cost_per_1m_usd
    )
    return {
        "n_incidents": n,
        "rca_type_accuracy_top1": sum(r["top1_type_correct"] for r in per_incident) / n,
        "rca_type_accuracy_top3": sum(r["top3_type_correct"] for r in per_incident) / n,
        "root_node_exact_match": sum(r["node_exact_match"] for r in per_incident) / n,
        "root_node_within_one_hop": sum(r["node_within_one_hop"] for r in per_incident) / n,
        "affected_node_f1_mean": sum(r["affected_node_f1"] for r in per_incident) / n,
        "latency_p50_seconds": _percentile(latencies, 0.50),
        "latency_p95_seconds": _percentile(latencies, 0.95),
        "mean_tokens_per_incident": sum(r["total_tokens"] for r in per_incident) / n,
        "mean_hypothetical_cost_per_incident_usd": mean_cost,
        "mean_diagnostic_loops": sum(r["num_diagnostic_loops"] for r in per_incident) / n,
    }
