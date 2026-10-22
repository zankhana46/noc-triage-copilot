"""Top-level entry point: builds the alarm-window state, invokes the
compiled LangGraph pipeline, times/counts everything, and logs the run to
MLflow (latency, tokens, tool calls — see SPEC.md 6.2).
"""

from __future__ import annotations

import os
import time
import uuid
from typing import Any

import mlflow
from dotenv import load_dotenv
from langgraph.graph.state import CompiledStateGraph

from noc_copilot.agents.runbook_retriever import DEFAULT_RETRIEVAL_MODE

load_dotenv()
mlflow.set_tracking_uri(os.environ.get("MLFLOW_TRACKING_URI", "sqlite:///mlflow.db"))
mlflow.set_experiment("noc-triage")


def strip_ground_truth(alarms: list[dict]) -> list[dict]:
    """Removes incident_id (hidden ground truth) before alarms ever enter
    agent state, so a prompt can never see it by accident."""
    return [{k: v for k, v in a.items() if k != "incident_id"} for a in alarms]


def _aggregate_trace(trace: list[dict]) -> dict[str, Any]:
    prompt_tokens = sum(t.get("prompt_tokens") or 0 for t in trace)
    completion_tokens = sum(t.get("completion_tokens") or 0 for t in trace)
    return {
        "total_prompt_tokens": prompt_tokens,
        "total_completion_tokens": completion_tokens,
        "total_tokens": prompt_tokens + completion_tokens,
        "tool_calls": sum(t.get("tool_calls") or 0 for t in trace),
        "num_diagnostic_loops": sum(1 for t in trace if t.get("node") == "topology_analyst"),
    }


def invoke_graph(
    graph: CompiledStateGraph,
    alarm_window: list[dict],
    *,
    use_graph_tools: bool = True,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    max_diagnostic_loops: int = 2,
) -> tuple[dict[str, Any], float]:
    """Lower-level than run_triage: strips ground truth, invokes the
    compiled graph, and returns (raw_final_state, latency_seconds) with no
    telemetry aggregation or MLflow logging. Used directly by evaluation
    ablations that need to inspect or reuse intermediate state (e.g. the
    retrieval-mode ablation reruns only the retriever+writer tail on the
    same diagnosis, rather than re-running the whole pipeline)."""
    initial_state: dict[str, Any] = {
        "alarms": strip_ground_truth(alarm_window),
        "use_graph_tools": use_graph_tools,
        "retrieval_mode": retrieval_mode,
        "max_diagnostic_loops": max_diagnostic_loops,
        "loop_count": 0,
        "trace": [],
    }
    start = time.perf_counter()
    final_state = graph.invoke(initial_state, config={"recursion_limit": 50})
    return final_state, time.perf_counter() - start


def finalize_result(final_state: dict[str, Any], latency_seconds: float) -> dict[str, Any]:
    """Shapes a raw graph final_state (or a manually-patched one, as the
    retrieval-mode ablation builds) into run_triage()'s result format."""
    telemetry = _aggregate_trace(final_state.get("trace", []))
    return {
        "report": final_state.get("report"),
        "trace": final_state.get("trace", []),
        "evidence_path": final_state.get("evidence_path", []),
        "root_cause_candidates": final_state.get("root_cause_candidates", []),
        "retrieved_chunks": final_state.get("retrieved_chunks", []),
        "latency_seconds": latency_seconds,
        **telemetry,
    }


def run_triage(
    graph: CompiledStateGraph,
    alarm_window: list[dict],
    *,
    use_graph_tools: bool = True,
    retrieval_mode: str = DEFAULT_RETRIEVAL_MODE,
    max_diagnostic_loops: int = 2,
    incident_id: str | None = None,
    log_to_mlflow: bool = True,
) -> dict[str, Any]:
    """Runs one alarm window through the full triage pipeline.

    Returns {"report": TriageReport dict | None, "trace": [...],
    "latency_seconds": float, **aggregated telemetry}.
    """
    final_state, latency_seconds = invoke_graph(
        graph, alarm_window, use_graph_tools=use_graph_tools,
        retrieval_mode=retrieval_mode, max_diagnostic_loops=max_diagnostic_loops,
    )
    result = finalize_result(final_state, latency_seconds)
    telemetry = {k: result[k] for k in (
        "total_prompt_tokens", "total_completion_tokens", "total_tokens",
        "tool_calls", "num_diagnostic_loops",
    )}

    if log_to_mlflow:
        with mlflow.start_run(run_name=f"triage-{incident_id or uuid.uuid4().hex[:8]}"):
            mlflow.log_params(
                {
                    "use_graph_tools": use_graph_tools,
                    "retrieval_mode": retrieval_mode,
                    "max_diagnostic_loops": max_diagnostic_loops,
                }
            )
            mlflow.log_metrics(
                {
                    "latency_seconds": latency_seconds,
                    "confidence": final_state.get("confidence", 0.0),
                    **{k: v for k, v in telemetry.items()},
                }
            )
            mlflow.log_dict(result["report"] or {}, "report.json")
            mlflow.log_dict(result["trace"], "trace.json")

    return result
