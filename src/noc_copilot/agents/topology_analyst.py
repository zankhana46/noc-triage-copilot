"""Topology Analyst node: uses find_common_ancestor (the key RCA signal)
plus the other graph tools to propose ranked root-cause node candidates
with evidence paths, for the Diagnostician to reason over.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from neo4j import Driver

from noc_copilot.agents.state import RootCauseCandidate, TriageState
from noc_copilot.graph.tools import (
    find_common_ancestor,
    get_downstream,
    get_node,
    get_redundancy,
    get_upstream_path,
)

MAX_CANDIDATES = 8


def make_topology_analyst_node(driver: Driver) -> Callable[[TriageState], dict[str, Any]]:
    def topology_analyst(state: TriageState) -> dict[str, Any]:
        loop_count = state.get("loop_count", 0) + 1
        node_ids = state.get("correlated_node_ids", [])
        correlated_alarms = state.get("correlated_alarms", [])
        use_graph_tools = state.get("use_graph_tools", True)

        if not use_graph_tools or not node_ids:
            return {
                "root_cause_candidates": [],
                "evidence_path": [],
                "loop_count": loop_count,
                "trace": [{"node": "topology_analyst", "skipped": True, "loop_count": loop_count}],
            }

        lca = find_common_ancestor(driver, node_ids)
        anchor_id = lca["id"] if lca else node_ids[0]

        # on a retry pass (the Diagnostician asked for more evidence), widen
        # the search one hop further toward the core
        if loop_count > 1:
            anchor_path = get_upstream_path(driver, anchor_id)
            if len(anchor_path) > 1:
                anchor_id = anchor_path[1]["id"]

        evidence_path = get_upstream_path(driver, anchor_id)
        candidates: dict[str, RootCauseCandidate] = {}

        def add_candidate(node: dict, reason: str) -> None:
            node_id = node["id"]
            if node_id in candidates:
                return
            alarms_for_node = [a for a in correlated_alarms if a["node_id"] == node_id]
            redundancy = get_redundancy(driver, node_id)
            depth = len(get_upstream_path(driver, node_id)) - 1
            candidates[node_id] = {
                "node_id": node_id,
                "node_type": node["type"],
                "vendor": node["vendor"],
                "site_id": node.get("site_id"),
                "has_direct_alarm": bool(alarms_for_node),
                "earliest_alarm_ts": min((a["timestamp"] for a in alarms_for_node), default=None),
                "depth_from_core": depth,
                "has_redundant_uplink": redundancy["has_redundant_uplink"],
                "rank_reason": reason,
            }

        for node_id in node_ids:
            node = get_node(driver, node_id)
            if node:
                add_candidate(node, "directly alarming node")

        if lca:
            add_candidate(lca, "lowest common ancestor of the alarming nodes")

        site_ids = {
            node["site_id"]
            for node_id in node_ids
            if (node := get_node(driver, node_id)) and node.get("site_id")
        }
        if len(site_ids) == 1 and lca:
            (site_id,) = site_ids
            for sibling in get_downstream(driver, anchor_id):
                if sibling.get("site_id") == site_id:
                    add_candidate(sibling, "equipment co-located at the same site")

        def sort_key(c: RootCauseCandidate) -> tuple[int, str, int]:
            return (
                0 if c["has_direct_alarm"] else 1,
                c["earliest_alarm_ts"] or "9999",
                -c["depth_from_core"],
            )

        ranked = sorted(candidates.values(), key=sort_key)[:MAX_CANDIDATES]

        return {
            "root_cause_candidates": ranked,
            "evidence_path": evidence_path,
            "loop_count": loop_count,
            "trace": [
                {
                    "node": "topology_analyst",
                    "loop_count": loop_count,
                    "anchor_node_id": anchor_id,
                    "num_candidates": len(ranked),
                    # find_common_ancestor + get_upstream_path, plus 2 lookups per candidate
                    "tool_calls": 2 + len(candidates) * 2,
                }
            ],
        }

    return topology_analyst
