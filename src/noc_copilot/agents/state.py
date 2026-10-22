"""Shared state and structured-output schemas for the LangGraph triage
pipeline (Phase 3). Final output matches SPEC.md section 6.1 exactly:
incident_summary, root_cause_node, root_cause_type, top3_hypotheses,
affected_nodes, remediation_steps, citations, confidence.
"""

from __future__ import annotations

import operator
from typing import Annotated, Literal, TypedDict, get_args

from pydantic import BaseModel, Field

from noc_copilot.simulator.schema import FailureType

RootCauseType = Literal[
    "fiber_cut", "site_power_failure", "router_hardware_fault", "sync_loss",
    "microwave_degradation", "cell_congestion", "cooling_failure", "core_overload",
    "unknown",
]
assert set(get_args(RootCauseType)) - {"unknown"} == {ft.value for ft in FailureType}


class Hypothesis(BaseModel):
    root_cause_type: RootCauseType
    root_cause_node: str | None = None
    confidence: float = Field(ge=0.0, le=1.0)
    rationale: str


class DiagnosticianOutput(BaseModel):
    hypotheses: list[Hypothesis] = Field(min_length=1, max_length=3)
    needs_more_evidence: bool


class Citation(BaseModel):
    source: str  # "alarm:<alarm_id>" | "topology:<node_id>" | "runbook:<chunk_id>"
    detail: str


class RemediationOutput(BaseModel):
    incident_summary: str
    remediation_steps: list[str]
    citations: list[Citation]


class TriageReport(BaseModel):
    incident_summary: str
    root_cause_node: str | None
    root_cause_type: RootCauseType
    top3_hypotheses: list[Hypothesis]
    affected_nodes: list[str]
    remediation_steps: list[str]
    citations: list[Citation]
    confidence: float = Field(ge=0.0, le=1.0)


class RootCauseCandidate(TypedDict):
    node_id: str
    node_type: str
    vendor: str
    site_id: str | None
    has_direct_alarm: bool
    earliest_alarm_ts: str | None
    depth_from_core: int
    has_redundant_uplink: bool
    rank_reason: str


class TriageState(TypedDict, total=False):
    # --- input ---
    alarms: list[dict]  # raw alarm dicts (hides ground-truth incident_id)
    use_graph_tools: bool
    retrieval_mode: str
    max_diagnostic_loops: int

    # --- Alarm Correlator ---
    correlated_node_ids: list[str]
    correlated_alarms: list[dict]
    noise_filtered_count: int

    # --- Topology Analyst ---
    root_cause_candidates: list[RootCauseCandidate]
    evidence_path: list[dict]
    loop_count: int

    # --- Diagnostician ---
    hypotheses: list[dict]
    confidence: float

    # --- Runbook Retriever ---
    retrieved_chunks: list[dict]

    # --- Remediation Writer / final ---
    report: dict

    # --- telemetry (appended across nodes) ---
    trace: Annotated[list[dict], operator.add]
