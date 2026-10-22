"""Unit tests for Phase 3's deterministic agent logic (no live Neo4j or
LLM calls needed): alarm correlation, diagnostician routing, and the
custom ground-truth eval metrics."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from noc_copilot.agents.correlator import _dedupe, _time_cluster
from noc_copilot.agents.diagnostician import (
    CONFIDENCE_THRESHOLD,
    DEFAULT_MAX_DIAGNOSTIC_LOOPS,
    route_after_diagnostician,
)
from noc_copilot.eval.triage_eval import _percentile, affected_node_f1, aggregate_scores

BASE = datetime(2026, 1, 1, tzinfo=UTC)


def _alarm(node_id: str, alarm_type: str, offset_seconds: float) -> dict:
    return {
        "node_id": node_id,
        "alarm_type": alarm_type,
        "timestamp": (BASE + timedelta(seconds=offset_seconds)).isoformat(),
        "severity": "major",
        "message": f"{alarm_type} at {node_id}",
    }


def test_dedupe_keeps_earliest_occurrence_per_node_and_type():
    alarms = [
        _alarm("csr-1", "link_down", 10),
        _alarm("csr-1", "link_down", 0),  # flap: earlier duplicate
        _alarm("csr-1", "s1_ng_failure", 5),
    ]
    deduped = _dedupe(alarms)
    assert len(deduped) == 2
    link_down = next(a for a in deduped if a["alarm_type"] == "link_down")
    assert link_down["timestamp"] == BASE.isoformat()


def test_time_cluster_splits_on_large_gaps():
    alarms = [
        _alarm("a", "x", 0),
        _alarm("a", "y", 10),
        _alarm("b", "z", 500),  # far beyond the gap threshold
    ]
    clusters = _time_cluster(alarms, gap_seconds=120)
    assert len(clusters) == 2
    assert len(clusters[0]) == 2
    assert len(clusters[1]) == 1


def test_time_cluster_empty_input():
    assert _time_cluster([], gap_seconds=120) == []


def test_route_after_diagnostician_loops_on_low_confidence():
    state = {"confidence": 0.3, "loop_count": 1, "max_diagnostic_loops": 2}
    assert route_after_diagnostician(state) == "topology_analyst"


def test_route_after_diagnostician_stops_at_max_loops():
    state = {
        "confidence": 0.3,
        "loop_count": DEFAULT_MAX_DIAGNOSTIC_LOOPS,
        "max_diagnostic_loops": DEFAULT_MAX_DIAGNOSTIC_LOOPS,
    }
    assert route_after_diagnostician(state) == "runbook_retriever"


def test_route_after_diagnostician_proceeds_on_high_confidence():
    state = {"confidence": CONFIDENCE_THRESHOLD + 0.1, "loop_count": 0, "max_diagnostic_loops": 2}
    assert route_after_diagnostician(state) == "runbook_retriever"


def test_affected_node_f1_perfect_match():
    result = affected_node_f1(["a", "b"], ["a", "b"])
    assert result == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_affected_node_f1_partial_overlap():
    result = affected_node_f1(["a", "b", "c"], ["a", "b", "d"])
    assert result["precision"] == 2 / 3
    assert result["recall"] == 2 / 3


def test_affected_node_f1_both_empty_is_trivially_correct():
    assert affected_node_f1([], []) == {"precision": 1.0, "recall": 1.0, "f1": 1.0}


def test_affected_node_f1_one_sided_empty_scores_zero():
    assert affected_node_f1(["a"], [])["f1"] == 0.0
    assert affected_node_f1([], ["a"])["f1"] == 0.0


def test_percentile_basic():
    values = [1.0, 2.0, 3.0, 4.0, 5.0]
    assert _percentile(values, 0.5) == 3.0
    assert _percentile([], 0.5) == 0.0


def test_aggregate_scores_empty():
    assert aggregate_scores([]) == {"n_incidents": 0}


def test_aggregate_scores_computes_rates_and_cost():
    rows = [
        {
            "top1_type_correct": True,
            "top3_type_correct": True,
            "node_exact_match": True,
            "node_within_one_hop": True,
            "affected_node_f1": 1.0,
            "latency_seconds": 1.0,
            "prompt_tokens": 1_000_000,
            "completion_tokens": 1_000_000,
            "total_tokens": 2_000_000,
            "num_diagnostic_loops": 1,
        },
        {
            "top1_type_correct": False,
            "top3_type_correct": True,
            "node_exact_match": False,
            "node_within_one_hop": True,
            "affected_node_f1": 0.5,
            "latency_seconds": 2.0,
            "prompt_tokens": 0,
            "completion_tokens": 0,
            "total_tokens": 0,
            "num_diagnostic_loops": 1,
        },
    ]
    agg = aggregate_scores(rows, input_cost_per_1m_usd=1.0, output_cost_per_1m_usd=2.0)
    assert agg["n_incidents"] == 2
    assert agg["rca_type_accuracy_top1"] == 0.5
    assert agg["rca_type_accuracy_top3"] == 1.0
    assert agg["root_node_exact_match"] == 0.5
    assert agg["root_node_within_one_hop"] == 1.0
    assert agg["affected_node_f1_mean"] == 0.75
    # mean prompt tokens = 500_000 -> cost = 0.5 * 1.0 = 0.5; mean completion
    # tokens = 500_000 -> cost = 0.5 * 2.0 = 1.0; total = 1.5
    assert agg["mean_hypothetical_cost_per_incident_usd"] == 1.5
