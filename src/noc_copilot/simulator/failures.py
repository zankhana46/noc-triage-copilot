"""Failure injection: for a chosen root-cause node, propagate realistic
downstream alarms through the real topology graph, respecting redundancy
(a dual-homed node does not lose connectivity from a single failed uplink).
"""

from __future__ import annotations

import random
from datetime import datetime, timedelta

import networkx as nx

from noc_copilot.simulator.alarms import build_alarm
from noc_copilot.simulator.schema import Alarm, FailureType, Node, NodeType, Severity

# Node types eligible as a root cause for each non-link failure type.
ROOT_NODE_TYPES: dict[FailureType, list[NodeType]] = {
    FailureType.SITE_POWER_FAILURE: [NodeType.POWER],
    FailureType.ROUTER_HARDWARE_FAULT: [
        NodeType.CORE_ROUTER,
        NodeType.AGG_ROUTER,
        NodeType.PREAGG_ROUTER,
        NodeType.CSR,
    ],
    FailureType.SYNC_LOSS: [NodeType.SYNC],
    FailureType.MICROWAVE_DEGRADATION: [NodeType.CSR],
    FailureType.CELL_CONGESTION: [NodeType.CELL],
    FailureType.COOLING_FAILURE: [NodeType.POWER],
    FailureType.CORE_OVERLOAD: [NodeType.AMF, NodeType.UPF, NodeType.CORE_ROUTER],
}
# fiber_cut roots at an *edge* (agg->preagg or preagg->csr); see
# pick_fiber_cut_target below instead of ROOT_NODE_TYPES.
FIBER_CUT_LAYER_TYPES = [NodeType.PREAGG_ROUTER, NodeType.CSR]


def downstream_of(
    graph: nx.DiGraph, node_id: str, types: list[NodeType] | None = None
) -> list[str]:
    descendants = nx.descendants(graph, node_id)
    if types is None:
        return sorted(descendants)
    return sorted(d for d in descendants if graph.nodes[d]["type"] in {t.value for t in types})


def has_redundant_uplink(graph: nx.DiGraph, node_id: str) -> bool:
    """True if node_id has more than one parent, i.e. losing one uplink
    still leaves a path to the core."""
    return graph.in_degree(node_id) > 1


def pick_fiber_cut_target(
    graph: nx.DiGraph, candidate_nodes: list[str], rng: random.Random
) -> str | None:
    """Pick a node whose incoming fiber link(s) we'll cut. Prefers
    single-homed targets so the cut actually isolates something; falls
    back to a dual-homed target (protected — no downstream outage) if
    that's all the candidate pool offers."""
    eligible = [
        n
        for n in candidate_nodes
        if graph.nodes[n]["type"] in {t.value for t in FIBER_CUT_LAYER_TYPES}
        and any(graph.edges[p, n]["link_type"] == "fiber" for p in graph.predecessors(n))
    ]
    if not eligible:
        return None
    single_homed = [n for n in eligible if not has_redundant_uplink(graph, n)]
    pool = single_homed or eligible
    return rng.choice(pool)


def _jitter(base: datetime, delay_range: tuple[float, float], rng: random.Random) -> datetime:
    return base + timedelta(seconds=rng.uniform(*delay_range))


def _node(nodes_by_id: dict[str, Node], node_id: str) -> Node:
    return nodes_by_id[node_id]


def _gen_fiber_cut(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms: list[Alarm] = []
    affected = [root_id]
    isolated = not has_redundant_uplink(graph, root_id)

    alarms.append(build_alarm(_node(nodes_by_id, root_id), "link_down", start, rng, incident_id))

    if not isolated:
        # protected by a redundant uplink: no downstream impact
        return alarms, affected

    root_type = NodeType(graph.nodes[root_id]["type"])
    if root_type == NodeType.PREAGG_ROUTER:
        alarms.append(
            build_alarm(
                _node(nodes_by_id, root_id),
                "backhaul_failure",
                _jitter(start, jitter, rng),
                rng,
                incident_id,
            )
        )
        csrs = downstream_of(graph, root_id, [NodeType.CSR])
    else:  # root_type == CSR: the cut already isolates just this site
        csrs = [root_id]

    for csr_id in csrs:
        if has_redundant_uplink(graph, csr_id):
            continue
        affected.append(csr_id)
        t = _jitter(start, (jitter[0], jitter[1] * 1.5), rng)
        alarms.append(build_alarm(_node(nodes_by_id, csr_id), "s1_ng_failure", t, rng, incident_id))
        for cell_id in downstream_of(graph, csr_id, [NodeType.CELL]):
            affected.append(cell_id)
            ct = _jitter(t, jitter, rng)
            alarms.append(
                build_alarm(_node(nodes_by_id, cell_id), "cell_outage", ct, rng, incident_id)
            )
    return alarms, affected


def _gen_site_power_failure(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [build_alarm(_node(nodes_by_id, root_id), "mains_failure", start, rng, incident_id)]
    affected = [root_id]

    t_battery = _jitter(start, (jitter[0] * 3, jitter[1] * 3), rng)
    alarms.append(
        build_alarm(
            _node(nodes_by_id, root_id),
            "battery_low",
            t_battery,
            rng,
            incident_id,
            severity=Severity.CRITICAL,
        )
    )

    t_site_down = _jitter(t_battery, (jitter[0] * 2, jitter[1] * 2), rng)
    csr_id = next(
        iter(
            downstream_of(graph, root_id, [NodeType.CSR])
            or [p for p in graph.predecessors(root_id)]
        ),
        None,
    )
    if csr_id is None:
        return alarms, affected
    affected.append(csr_id)
    alarms.append(
        build_alarm(_node(nodes_by_id, csr_id), "site_down", t_site_down, rng, incident_id)
    )
    for cell_id in downstream_of(graph, csr_id, [NodeType.CELL]):
        affected.append(cell_id)
        ct = _jitter(t_site_down, jitter, rng)
        alarms.append(build_alarm(_node(nodes_by_id, cell_id), "cell_outage", ct, rng, incident_id))
    return alarms, affected


def _gen_router_hardware_fault(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [build_alarm(_node(nodes_by_id, root_id), "card_failure", start, rng, incident_id)]
    affected = [root_id]

    children = list(graph.successors(root_id))
    for child_id in children:
        t_flap = _jitter(start, jitter, rng)
        alarms.append(
            build_alarm(_node(nodes_by_id, child_id), "link_flap", t_flap, rng, incident_id)
        )
        affected.append(child_id)

    downstream_sample = downstream_of(graph, root_id)
    sample_size = max(1, len(downstream_sample) // 4)
    for node_id in rng.sample(downstream_sample, k=min(sample_size, len(downstream_sample))):
        affected.append(node_id)
        t = _jitter(start, (jitter[0] * 2, jitter[1] * 2), rng)
        alarms.append(
            build_alarm(_node(nodes_by_id, node_id), "throughput_degradation", t, rng, incident_id)
        )
    return alarms, affected


def _gen_sync_loss(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [
        build_alarm(_node(nodes_by_id, root_id), "sync_loss_gps_ptp", start, rng, incident_id)
    ]
    affected = [root_id]
    site_id = nodes_by_id[root_id].site_id
    site_cells = [
        n
        for n, d in graph.nodes(data=True)
        if d.get("site_id") == site_id and d["type"] == NodeType.CELL.value
    ]
    for cell_id in site_cells:
        affected.append(cell_id)
        t1 = _jitter(start, jitter, rng)
        alarms.append(
            build_alarm(_node(nodes_by_id, cell_id), "handover_failure", t1, rng, incident_id)
        )
        t2 = _jitter(t1, jitter, rng)
        alarms.append(
            build_alarm(_node(nodes_by_id, cell_id), "throughput_degradation", t2, rng, incident_id)
        )
    return alarms, affected


def _gen_microwave_degradation(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [build_alarm(_node(nodes_by_id, root_id), "high_ber", start, rng, incident_id)]
    affected = [root_id]
    descendants = downstream_of(graph, root_id, [NodeType.GNB, NodeType.CELL])
    for node_id in descendants:
        affected.append(node_id)
        t1 = _jitter(start, jitter, rng)
        alarms.append(
            build_alarm(
                _node(nodes_by_id, node_id), "intermittent_packet_loss", t1, rng, incident_id
            )
        )
        t2 = _jitter(t1, jitter, rng)
        alarms.append(
            build_alarm(_node(nodes_by_id, node_id), "latency_alarm", t2, rng, incident_id)
        )
    return alarms, affected


def _gen_cell_congestion(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [
        build_alarm(_node(nodes_by_id, root_id), "high_prb_utilization", start, rng, incident_id)
    ]
    affected = [root_id]
    t1 = _jitter(start, jitter, rng)
    alarms.append(
        build_alarm(_node(nodes_by_id, root_id), "rrc_setup_failure", t1, rng, incident_id)
    )
    t2 = _jitter(t1, jitter, rng)
    alarms.append(build_alarm(_node(nodes_by_id, root_id), "throughput_drop", t2, rng, incident_id))
    return alarms, affected


def _gen_cooling_failure(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [build_alarm(_node(nodes_by_id, root_id), "high_temperature", start, rng, incident_id)]
    affected = [root_id]
    t_fan = _jitter(start, jitter, rng)
    alarms.append(build_alarm(_node(nodes_by_id, root_id), "fan_failure", t_fan, rng, incident_id))

    if rng.random() < 0.5:  # "possible" equipment shutdown
        t_shutdown = _jitter(t_fan, (jitter[0] * 2, jitter[1] * 2), rng)
        csr_id = next(iter([p for p in graph.predecessors(root_id)]), None)
        if csr_id is not None:
            affected.append(csr_id)
            alarms.append(
                build_alarm(
                    _node(nodes_by_id, csr_id), "equipment_shutdown", t_shutdown, rng, incident_id
                )
            )
            for cell_id in downstream_of(graph, csr_id, [NodeType.CELL]):
                affected.append(cell_id)
                ct = _jitter(t_shutdown, jitter, rng)
                alarms.append(
                    build_alarm(_node(nodes_by_id, cell_id), "cell_outage", ct, rng, incident_id)
                )
    return alarms, affected


def _gen_core_overload(graph, nodes_by_id, root_id, start, jitter, rng, incident_id):
    alarms = [
        build_alarm(
            _node(nodes_by_id, root_id), "elevated_signalling_latency", start, rng, incident_id
        )
    ]
    affected = [root_id]
    sites = downstream_of(graph, root_id, [NodeType.CSR])
    sample_size = max(1, len(sites) // 3)
    for csr_id in rng.sample(sites, k=min(sample_size, len(sites))):
        affected.append(csr_id)
        t = _jitter(start, (jitter[0] * 2, jitter[1] * 2), rng)
        alarms.append(
            build_alarm(
                _node(nodes_by_id, csr_id), "attach_registration_failure", t, rng, incident_id
            )
        )
    return alarms, affected


_GENERATORS = {
    FailureType.FIBER_CUT: _gen_fiber_cut,
    FailureType.SITE_POWER_FAILURE: _gen_site_power_failure,
    FailureType.ROUTER_HARDWARE_FAULT: _gen_router_hardware_fault,
    FailureType.SYNC_LOSS: _gen_sync_loss,
    FailureType.MICROWAVE_DEGRADATION: _gen_microwave_degradation,
    FailureType.CELL_CONGESTION: _gen_cell_congestion,
    FailureType.COOLING_FAILURE: _gen_cooling_failure,
    FailureType.CORE_OVERLOAD: _gen_core_overload,
}


def inject_failure(
    graph: nx.DiGraph,
    nodes_by_id: dict[str, Node],
    failure_type: FailureType,
    root_id: str,
    window_start: datetime,
    jitter_seconds: tuple[float, float],
    rng: random.Random,
    incident_id: str,
) -> tuple[list[Alarm], list[str]]:
    """Returns (alarms, affected_node_ids) for a single injected failure."""
    generator = _GENERATORS[failure_type]
    alarms, affected = generator(
        graph, nodes_by_id, root_id, window_start, jitter_seconds, rng, incident_id
    )
    # dedupe while preserving order
    seen: set[str] = set()
    affected_unique: list[str] = []
    for n in affected:
        if n not in seen:
            seen.add(n)
            affected_unique.append(n)
    return alarms, affected_unique
