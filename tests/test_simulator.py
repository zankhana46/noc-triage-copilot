"""Phase 1 acceptance tests: propagation follows real topology paths, and
redundant links prevent downstream outages."""

from __future__ import annotations

import random
from datetime import datetime

import networkx as nx
import pytest

from noc_copilot.simulator.failures import (
    FIBER_CUT_LAYER_TYPES,
    has_redundant_uplink,
    inject_failure,
    pick_fiber_cut_target,
)
from noc_copilot.simulator.incidents import generate_incident_bundles
from noc_copilot.simulator.schema import FailureType, NodeType
from noc_copilot.simulator.topology import build_topology

SITE_COLOCATED_FAILURES = {
    FailureType.SITE_POWER_FAILURE,
    FailureType.SYNC_LOSS,
    FailureType.COOLING_FAILURE,
}
DESCENDANT_PROPAGATING_FAILURES = {
    FailureType.FIBER_CUT,
    FailureType.ROUTER_HARDWARE_FAULT,
    FailureType.MICROWAVE_DEGRADATION,
    FailureType.CORE_OVERLOAD,
}


def _small_cfg(
    agg_redundancy_fraction: float = 0.4,
    preagg_redundancy_fraction: float = 0.3,
) -> dict:
    return {
        "seed": 42,
        "topology": {
            "regions": ["northeast", "southeast", "midwest", "west"],
            "vendors": ["Northeon", "Vendorix", "Arcadia Networks"],
            "num_core_routers": 2,
            "num_amf": 1,
            "num_upf": 1,
            "num_agg_routers": 4,
            "preagg_per_agg": 2,
            "sites_per_preagg": 3,
            "cells_per_site": 3,
            "agg_redundancy_fraction": agg_redundancy_fraction,
            "preagg_redundancy_fraction": preagg_redundancy_fraction,
            "link_types": ["fiber", "microwave"],
            "microwave_fraction": 0.25,
        },
        "failures": {
            "jitter_seconds": [1, 5],
            "types": [ft.value for ft in FailureType],
        },
        "noise": {
            "background_fraction": 0.5,
            "flap_probability": 0.1,
            "flap_repeats": [2, 3],
            "duplicate_probability": 0.1,
        },
        "incidents": {
            "num_bundles": 60,
            "window_minutes": 30,
            "overlap_fraction": 0.1,
            "splits": {"train": 0.7, "dev": 0.15, "test": 0.15},
        },
    }


def test_topology_shape():
    cfg = _small_cfg()
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)

    tcfg = cfg["topology"]
    expected_preagg = tcfg["num_agg_routers"] * tcfg["preagg_per_agg"]
    expected_sites = expected_preagg * tcfg["sites_per_preagg"]

    assert len(set(n.id for n in nodes)) == len(nodes), "node ids must be unique"
    by_type = {}
    for n in nodes:
        by_type.setdefault(n.type, []).append(n)

    assert len(by_type[NodeType.AGG_ROUTER]) == tcfg["num_agg_routers"]
    assert len(by_type[NodeType.PREAGG_ROUTER]) == expected_preagg
    assert len(by_type[NodeType.CSR]) == expected_sites
    assert len(by_type[NodeType.GNB]) == expected_sites
    assert len(by_type[NodeType.CELL]) == expected_sites * tcfg["cells_per_site"]
    assert len(by_type[NodeType.POWER]) == expected_sites
    assert len(by_type[NodeType.SYNC]) == expected_sites

    # every site's CSR has exactly one GNB, one POWER, one SYNC child
    for csr in by_type[NodeType.CSR]:
        children = [nodes_by_id(nodes)[c].type for c in graph.successors(csr.id)]
        assert children.count(NodeType.GNB) == 1
        assert children.count(NodeType.POWER) == 1
        assert children.count(NodeType.SYNC) == 1


def nodes_by_id(nodes):
    return {n.id: n for n in nodes}


def test_fiber_cut_on_dual_homed_preagg_does_not_cascade():
    # force every preagg router to be dual-homed (redundant uplink)
    cfg = _small_cfg(preagg_redundancy_fraction=1.0)
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)
    nbi = nodes_by_id(nodes)

    preagg_ids = [n.id for n in nodes if n.type == NodeType.PREAGG_ROUTER]
    assert all(has_redundant_uplink(graph, p) for p in preagg_ids), (
        "fixture should be fully dual-homed"
    )

    root_id = preagg_ids[0]
    alarms, affected = inject_failure(
        graph,
        nbi,
        FailureType.FIBER_CUT,
        root_id,
        datetime(2026, 1, 1),
        (1, 5),
        rng,
        "test-incident",
    )
    assert affected == [root_id], "redundant uplink must prevent any downstream outage"
    assert len(alarms) == 1 and alarms[0].alarm_type == "link_down"


def test_fiber_cut_on_single_homed_preagg_cascades_through_real_topology():
    cfg = _small_cfg(preagg_redundancy_fraction=0.0)
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)
    nbi = nodes_by_id(nodes)

    preagg_ids = [n.id for n in nodes if n.type == NodeType.PREAGG_ROUTER]
    assert all(not has_redundant_uplink(graph, p) for p in preagg_ids)

    root_id = preagg_ids[0]
    descendants = nx.descendants(graph, root_id)
    alarms, affected = inject_failure(
        graph,
        nbi,
        FailureType.FIBER_CUT,
        root_id,
        datetime(2026, 1, 1),
        (1, 5),
        rng,
        "test-incident",
    )
    assert len(affected) > 1, "single-homed cut should cascade to descendants"
    for node_id in affected:
        assert node_id == root_id or node_id in descendants, (
            f"{node_id} is not actually downstream of {root_id} in the topology"
        )
    # every alarm's node must be the root or one of its real descendants
    for alarm in alarms:
        assert alarm.node_id == root_id or alarm.node_id in descendants


@pytest.mark.parametrize("failure_type", sorted(DESCENDANT_PROPAGATING_FAILURES, key=str))
def test_propagation_stays_within_real_descendants(failure_type):
    cfg = _small_cfg(preagg_redundancy_fraction=0.0, agg_redundancy_fraction=0.0)
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)
    nbi = nodes_by_id(nodes)

    from noc_copilot.simulator.failures import ROOT_NODE_TYPES

    if failure_type == FailureType.FIBER_CUT:
        eligible = [n.id for n in nodes if n.type in FIBER_CUT_LAYER_TYPES]
        root_id = pick_fiber_cut_target(graph, eligible, rng)
    else:
        eligible_types = {t.value for t in ROOT_NODE_TYPES[failure_type]}
        root_id = next(n.id for n in nodes if n.type in eligible_types)

    descendants = nx.descendants(graph, root_id)
    alarms, affected = inject_failure(
        graph,
        nbi,
        failure_type,
        root_id,
        datetime(2026, 1, 1),
        (1, 5),
        rng,
        "test-incident",
    )
    for node_id in affected:
        assert node_id == root_id or node_id in descendants


@pytest.mark.parametrize("failure_type", sorted(SITE_COLOCATED_FAILURES, key=str))
def test_site_colocated_failures_stay_within_the_site(failure_type):
    cfg = _small_cfg()
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)
    nbi = nodes_by_id(nodes)

    from noc_copilot.simulator.failures import ROOT_NODE_TYPES

    eligible_types = {t.value for t in ROOT_NODE_TYPES[failure_type]}
    root_node = next(n for n in nodes if n.type in eligible_types)

    _, affected = inject_failure(
        graph,
        nbi,
        failure_type,
        root_node.id,
        datetime(2026, 1, 1),
        (1, 5),
        rng,
        "test-incident",
    )
    for node_id in affected:
        assert nbi[node_id].site_id == root_node.site_id


def test_incident_splits_never_share_a_root_cause_node():
    cfg = _small_cfg()
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)
    bundles = generate_incident_bundles(graph, nodes, cfg, rng)

    roots_by_split: dict[str, set[str]] = {}
    for b in bundles:
        roots_by_split.setdefault(b.split, set()).add(b.root_cause_node)

    assert set(roots_by_split.keys()) == {"train", "dev", "test"}, (
        "a split came up with zero incidents"
    )
    splits = list(roots_by_split.keys())
    for i, a in enumerate(splits):
        for b in splits[i + 1 :]:
            overlap = roots_by_split[a] & roots_by_split[b]
            assert not overlap, f"root-cause node(s) {overlap} shared between {a} and {b}"


def test_incident_bundles_have_noise_mixed_in():
    cfg = _small_cfg()
    rng = random.Random(cfg["seed"])
    nodes, links, graph = build_topology(cfg, rng)
    bundles = generate_incident_bundles(graph, nodes, cfg, rng)

    assert len(bundles) > 0
    total_alarms = sum(len(b.alarms) for b in bundles)
    noise_alarms = sum(1 for b in bundles for a in b.alarms if a.incident_id is None)
    assert 0 < noise_alarms < total_alarms
