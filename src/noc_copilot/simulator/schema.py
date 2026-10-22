"""Typed data models shared across the simulator, graph loader, and agents."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel


class NodeType(StrEnum):
    AMF = "AMF"
    UPF = "UPF"
    CORE_ROUTER = "CORE_ROUTER"
    AGG_ROUTER = "AGG_ROUTER"
    PREAGG_ROUTER = "PREAGG_ROUTER"
    CSR = "CSR"
    GNB = "GNB"
    CELL = "CELL"
    POWER = "POWER"
    SYNC = "SYNC"


class Severity(StrEnum):
    CRITICAL = "critical"
    MAJOR = "major"
    MINOR = "minor"
    WARNING = "warning"


class FailureType(StrEnum):
    FIBER_CUT = "fiber_cut"
    SITE_POWER_FAILURE = "site_power_failure"
    ROUTER_HARDWARE_FAULT = "router_hardware_fault"
    SYNC_LOSS = "sync_loss"
    MICROWAVE_DEGRADATION = "microwave_degradation"
    CELL_CONGESTION = "cell_congestion"
    COOLING_FAILURE = "cooling_failure"
    CORE_OVERLOAD = "core_overload"


class Node(BaseModel):
    id: str
    type: NodeType
    region: str
    vendor: str
    site_id: str | None = None
    lat: float
    lon: float


class Link(BaseModel):
    source: str
    target: str
    link_type: str  # "fiber" | "microwave"
    capacity_mbps: int
    redundant: bool


class Alarm(BaseModel):
    alarm_id: str
    timestamp: str  # ISO 8601
    node_id: str
    alarm_type: str
    severity: Severity
    vendor_code: str
    message: str
    incident_id: str | None = None  # ground truth, hidden from the copilot


class IncidentBundle(BaseModel):
    incident_id: str
    failure_type: FailureType
    root_cause_node: str
    root_cause_type: NodeType
    affected_nodes: list[str]
    runbook_id: str
    window_start: str
    window_end: str
    alarms: list[Alarm]
    split: str  # "train" | "dev" | "test"
