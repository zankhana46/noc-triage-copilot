"""Incident bundle generation: sample root causes from topology-partitioned
splits, inject failures, overlay noise, and assemble ground-truth bundles.
"""

from __future__ import annotations

import random
import uuid
from datetime import UTC, datetime, timedelta

import networkx as nx

from noc_copilot.simulator.alarms import apply_flapping_and_duplicates, generate_background_noise
from noc_copilot.simulator.failures import (
    ROOT_NODE_TYPES,
    inject_failure,
    pick_fiber_cut_target,
)
from noc_copilot.simulator.runbooks import VENDOR_VARIANT_PLAN, runbook_id_for
from noc_copilot.simulator.schema import Alarm, FailureType, IncidentBundle, Node, NodeType

BASE_TIME = datetime(2026, 1, 1, tzinfo=UTC)
SIMULATION_SPAN_DAYS = 60

_VENDOR_VARIANT_LOOKUP = {(ft, vendor) for ft, vendor in VENDOR_VARIANT_PLAN}


def _apportion(n: int, weights: list[float]) -> list[int]:
    """Integer counts summing to n, proportional to weights (largest-
    remainder method), with a safety correction so that every category
    gets at least 1 whenever n >= len(weights). A per-item weighted random
    draw can plausibly leave a whole split empty when n is small (e.g. a
    handful of aggregation routers); this can't."""
    if n == 0:
        return [0] * len(weights)
    total_weight = sum(weights)
    raw = [n * w / total_weight for w in weights]
    counts = [int(r) for r in raw]
    remainder = n - sum(counts)
    by_fraction = sorted(range(len(weights)), key=lambda i: raw[i] - counts[i], reverse=True)
    for i in by_fraction[:remainder]:
        counts[i] += 1
    if n >= len(weights):
        for i in range(len(weights)):
            while counts[i] == 0:
                donor = max(range(len(weights)), key=lambda k: counts[k])
                counts[donor] -= 1
                counts[i] += 1
    return counts


def assign_splits(
    graph: nx.DiGraph, nodes: list[Node], cfg: dict, rng: random.Random
) -> dict[str, str]:
    """Partitions the topology by subtree so that no root-cause-eligible
    node pool overlaps between splits: each aggregation router's entire
    downstream subtree goes to one split, and core elements are
    independently assigned the same way. Within each group, the random
    seed decides *which* nodes land in which split, but the split sizes
    themselves are apportioned deterministically from the configured
    fractions so no split can come up empty by chance."""
    splits_cfg = cfg["incidents"]["splits"]
    split_names = list(splits_cfg.keys())
    weights = list(splits_cfg.values())

    split_of: dict[str, str] = {}

    def partition(ids: list[str]) -> dict[str, str]:
        shuffled = list(ids)
        rng.shuffle(shuffled)
        counts = _apportion(len(shuffled), weights)
        assignment: dict[str, str] = {}
        cursor = 0
        for split, count in zip(split_names, counts, strict=True):
            for node_id in shuffled[cursor : cursor + count]:
                assignment[node_id] = split
            cursor += count
        return assignment

    agg_routers = [n.id for n in nodes if n.type == NodeType.AGG_ROUTER]
    for agg_id, split in partition(agg_routers).items():
        split_of[agg_id] = split
        for descendant in nx.descendants(graph, agg_id):
            split_of[descendant] = split

    independent_types = {NodeType.AMF, NodeType.UPF, NodeType.CORE_ROUTER}
    independent_ids = [n.id for n in nodes if n.type in independent_types]
    split_of.update(partition(independent_ids))

    return split_of


def _runbook_id_for_root(failure_type: FailureType, root_node: Node) -> str:
    if (failure_type, root_node.vendor) in _VENDOR_VARIANT_LOOKUP:
        return runbook_id_for(failure_type, root_node.vendor)
    return runbook_id_for(failure_type)


def _candidate_pool(
    failure_type: FailureType,
    nodes_by_id: dict[str, Node],
    split_of: dict[str, str],
    target_split: str,
    graph: nx.DiGraph,
) -> list[str]:
    split_node_ids = [nid for nid, s in split_of.items() if s == target_split]
    if failure_type == FailureType.FIBER_CUT:
        return split_node_ids  # pick_fiber_cut_target does its own type filtering
    eligible_types = {t.value for t in ROOT_NODE_TYPES[failure_type]}
    return [nid for nid in split_node_ids if nodes_by_id[nid].type in eligible_types]


def generate_incident_bundles(
    graph: nx.DiGraph,
    nodes: list[Node],
    cfg: dict,
    rng: random.Random,
) -> list[IncidentBundle]:
    nodes_by_id = {n.id: n for n in nodes}
    split_of = assign_splits(graph, nodes, cfg, rng)
    icfg = cfg["incidents"]
    ncfg = cfg["noise"]
    jitter = tuple(cfg["failures"]["jitter_seconds"])
    failure_types = [FailureType(ft) for ft in cfg["failures"]["types"]]
    window_minutes = icfg["window_minutes"]
    num_bundles = icfg["num_bundles"]

    bundles: list[IncidentBundle] = []
    prior_windows: list[tuple[datetime, datetime]] = []
    attempts = 0
    max_attempts = num_bundles * 20

    while len(bundles) < num_bundles and attempts < max_attempts:
        attempts += 1
        target_split = rng.choices(
            list(icfg["splits"].keys()), weights=list(icfg["splits"].values()), k=1
        )[0]
        failure_type = failure_types[len(bundles) % len(failure_types)]

        pool = _candidate_pool(failure_type, nodes_by_id, split_of, target_split, graph)
        if not pool:
            continue
        if failure_type == FailureType.FIBER_CUT:
            root_id = pick_fiber_cut_target(graph, pool, rng)
        else:
            root_id = rng.choice(pool)
        if root_id is None:
            continue

        if rng.random() < icfg["overlap_fraction"] and prior_windows:
            base_start, base_end = rng.choice(prior_windows)
            window_start = base_start + timedelta(
                minutes=rng.uniform(-window_minutes / 2, window_minutes / 2)
            )
        else:
            offset_days = rng.uniform(0, SIMULATION_SPAN_DAYS)
            window_start = BASE_TIME + timedelta(days=offset_days, seconds=rng.uniform(0, 86400))
        window_end = window_start + timedelta(minutes=window_minutes)
        prior_windows.append((window_start, window_end))

        incident_id = str(uuid.uuid4())
        true_alarms, affected_nodes = inject_failure(
            graph, nodes_by_id, failure_type, root_id, window_start, jitter, rng, incident_id
        )
        true_alarms = apply_flapping_and_duplicates(
            true_alarms,
            ncfg["flap_probability"],
            tuple(ncfg["flap_repeats"]),
            ncfg["duplicate_probability"],
            rng,
        )
        noise_count = max(1, round(ncfg["background_fraction"] * len(true_alarms)))
        noise_alarms = generate_background_noise(nodes, window_start, window_end, noise_count, rng)

        all_alarms: list[Alarm] = sorted(true_alarms + noise_alarms, key=lambda a: a.timestamp)
        root_node = nodes_by_id[root_id]

        bundles.append(
            IncidentBundle(
                incident_id=incident_id,
                failure_type=failure_type,
                root_cause_node=root_id,
                root_cause_type=root_node.type,
                affected_nodes=affected_nodes,
                runbook_id=_runbook_id_for_root(failure_type, root_node),
                window_start=window_start.isoformat(),
                window_end=window_end.isoformat(),
                alarms=all_alarms,
                split=target_split,
            )
        )

    return bundles
