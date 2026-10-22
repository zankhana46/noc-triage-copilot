"""Alarm Correlator node: deduplicates, time-clusters, and (when graph
tools are enabled) topology-weights the alarm window to separate the real
incident from background noise — before anything is shown to an LLM.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime
from typing import Any

from neo4j import Driver

from noc_copilot.agents.state import TriageState
from noc_copilot.graph.tools import find_common_ancestor, get_upstream_path

DEDUPE_KEY = ("node_id", "alarm_type")
TIME_GAP_SECONDS = 120.0


def _dedupe(alarms: list[dict]) -> list[dict]:
    """Collapses flapping/duplicate alarms to their earliest occurrence
    per (node_id, alarm_type)."""
    earliest: dict[tuple[str, str], dict] = {}
    for alarm in sorted(alarms, key=lambda a: a["timestamp"]):
        key = (alarm["node_id"], alarm["alarm_type"])
        if key not in earliest:
            earliest[key] = alarm
    return sorted(earliest.values(), key=lambda a: a["timestamp"])


def _time_cluster(alarms: list[dict], gap_seconds: float) -> list[list[dict]]:
    clusters: list[list[dict]] = []
    current: list[dict] = []
    prev_ts: datetime | None = None
    for alarm in alarms:
        ts = datetime.fromisoformat(alarm["timestamp"])
        if prev_ts is not None and (ts - prev_ts).total_seconds() > gap_seconds:
            clusters.append(current)
            current = []
        current.append(alarm)
        prev_ts = ts
    if current:
        clusters.append(current)
    return clusters


def _topology_depth_score(driver: Driver, cluster: list[dict]) -> int:
    node_ids = sorted({a["node_id"] for a in cluster})
    lca = find_common_ancestor(driver, node_ids)
    if lca is None:
        return 0
    return len(get_upstream_path(driver, lca["id"])) - 1


def make_alarm_correlator_node(driver: Driver) -> Callable[[TriageState], dict[str, Any]]:
    def alarm_correlator(state: TriageState) -> dict[str, Any]:
        alarms = state["alarms"]
        use_graph_tools = state.get("use_graph_tools", True)

        deduped = _dedupe(alarms)
        clusters = _time_cluster(deduped, TIME_GAP_SECONDS)

        if use_graph_tools:

            def score(cluster: list[dict]) -> tuple[int, int]:
                return (len(cluster), _topology_depth_score(driver, cluster))
        else:

            def score(cluster: list[dict]) -> tuple[int, int]:
                return (len(cluster), 0)

        best = max(clusters, key=score) if clusters else []
        correlated_node_ids = sorted({a["node_id"] for a in best})

        return {
            "correlated_alarms": best,
            "correlated_node_ids": correlated_node_ids,
            "noise_filtered_count": len(alarms) - len(best),
            "trace": [
                {
                    "node": "alarm_correlator",
                    "input_alarm_count": len(alarms),
                    "deduped_count": len(deduped),
                    "clusters_found": len(clusters),
                    "selected_cluster_size": len(best),
                }
            ],
        }

    return alarm_correlator
