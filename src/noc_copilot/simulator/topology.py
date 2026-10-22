"""Hierarchical mobile network topology generator.

Builds a core -> aggregation -> pre-aggregation -> cell-site hierarchy as a
networkx.DiGraph, edges directed parent -> child (upstream -> downstream).
A node is "redundant" when it has more than one upstream parent; both of
its parent links are then marked `redundant=True` since each is a viable
path to the core on its own.
"""

from __future__ import annotations

import random

import networkx as nx

from noc_copilot.simulator.schema import Link, Node, NodeType

REGION_CENTERS = {
    "northeast": (42.0, -71.0),
    "southeast": (33.0, -84.0),
    "midwest": (41.5, -87.5),
    "west": (37.5, -122.0),
}

FIBER_CAPACITY_BY_LAYER = {
    "core": 100_000,
    "agg": 40_000,
    "preagg": 10_000,
    "access": 1_000,
}
MICROWAVE_CAPACITY_MBPS = [100, 300, 600]


def _jittered_latlon(region: str, rng: random.Random) -> tuple[float, float]:
    base_lat, base_lon = REGION_CENTERS[region]
    return (
        round(base_lat + rng.uniform(-2.0, 2.0), 4),
        round(base_lon + rng.uniform(-2.0, 2.0), 4),
    )


def build_topology(cfg: dict, rng: random.Random) -> tuple[list[Node], list[Link], nx.DiGraph]:
    """Returns (nodes, links, graph). `graph` edges carry the same attributes as Link."""
    tcfg = cfg["topology"]
    regions: list[str] = tcfg["regions"]
    vendors: list[str] = tcfg["vendors"]

    nodes: dict[str, Node] = {}
    links: list[Link] = []
    graph = nx.DiGraph()

    def add_node(node: Node) -> None:
        nodes[node.id] = node
        graph.add_node(node.id, **node.model_dump())

    def add_link(
        source: str, target: str, link_type: str, capacity_mbps: int, redundant: bool
    ) -> None:
        link = Link(
            source=source,
            target=target,
            link_type=link_type,
            capacity_mbps=capacity_mbps,
            redundant=redundant,
        )
        links.append(link)
        graph.add_edge(source, target, **link.model_dump())

    # --- core ---
    core_routers = []
    for i in range(tcfg["num_core_routers"]):
        region = regions[i % len(regions)]
        node_id = f"core-rtr-{i + 1}"
        add_node(
            Node(
                id=node_id,
                type=NodeType.CORE_ROUTER,
                region=region,
                vendor=rng.choice(vendors),
                lat=_jittered_latlon(region, rng)[0],
                lon=_jittered_latlon(region, rng)[1],
            )
        )
        core_routers.append(node_id)

    # core routers form a backbone ring so the core itself has no single
    # point of failure
    for i, src in enumerate(core_routers):
        dst = core_routers[(i + 1) % len(core_routers)]
        if len(core_routers) > 1:
            add_link(src, dst, "fiber", FIBER_CAPACITY_BY_LAYER["core"], redundant=True)
            add_link(dst, src, "fiber", FIBER_CAPACITY_BY_LAYER["core"], redundant=True)

    for kind, count, prefix in (
        (NodeType.AMF, tcfg["num_amf"], "amf"),
        (NodeType.UPF, tcfg["num_upf"], "upf"),
    ):
        for i in range(count):
            region = rng.choice(regions)
            lat, lon = _jittered_latlon(region, rng)
            node_id = f"{prefix}-{i + 1}"
            add_node(
                Node(
                    id=node_id,
                    type=kind,
                    region=region,
                    vendor=rng.choice(vendors),
                    lat=lat,
                    lon=lon,
                )
            )
            homes = rng.sample(core_routers, k=min(2, len(core_routers)))
            for parent in homes:
                add_link(
                    parent,
                    node_id,
                    "fiber",
                    FIBER_CAPACITY_BY_LAYER["core"],
                    redundant=len(homes) > 1,
                )

    # --- aggregation ---
    agg_routers = []
    for i in range(tcfg["num_agg_routers"]):
        region = regions[i % len(regions)]
        lat, lon = _jittered_latlon(region, rng)
        node_id = f"agg-rtr-{i + 1}"
        add_node(
            Node(
                id=node_id,
                type=NodeType.AGG_ROUTER,
                region=region,
                vendor=rng.choice(vendors),
                lat=lat,
                lon=lon,
            )
        )
        agg_routers.append(node_id)

        primary = rng.choice(core_routers)
        dual_homed = rng.random() < tcfg["agg_redundancy_fraction"] and len(core_routers) > 1
        add_link(primary, node_id, "fiber", FIBER_CAPACITY_BY_LAYER["core"], redundant=dual_homed)
        if dual_homed:
            secondary = rng.choice([c for c in core_routers if c != primary])
            add_link(secondary, node_id, "fiber", FIBER_CAPACITY_BY_LAYER["core"], redundant=True)

    # --- pre-aggregation ---
    preagg_routers = []
    for agg in agg_routers:
        agg_region = nodes[agg].region
        for j in range(tcfg["preagg_per_agg"]):
            lat, lon = _jittered_latlon(agg_region, rng)
            node_id = f"preagg-rtr-{agg.split('-')[-1]}-{j + 1}"
            add_node(
                Node(
                    id=node_id,
                    type=NodeType.PREAGG_ROUTER,
                    region=agg_region,
                    vendor=rng.choice(vendors),
                    lat=lat,
                    lon=lon,
                )
            )
            preagg_routers.append(node_id)

            dual_homed = rng.random() < tcfg["preagg_redundancy_fraction"] and len(agg_routers) > 1
            add_link(agg, node_id, "fiber", FIBER_CAPACITY_BY_LAYER["agg"], redundant=dual_homed)
            if dual_homed:
                secondary_agg = rng.choice([a for a in agg_routers if a != agg])
                add_link(
                    secondary_agg, node_id, "fiber", FIBER_CAPACITY_BY_LAYER["agg"], redundant=True
                )

    # --- cell sites ---
    site_counter = 0
    for preagg in preagg_routers:
        preagg_region = nodes[preagg].region
        for _ in range(tcfg["sites_per_preagg"]):
            site_counter += 1
            site_id = f"site-{site_counter}"
            lat, lon = _jittered_latlon(preagg_region, rng)
            vendor = rng.choice(vendors)

            csr_id = f"csr-{site_counter}"
            add_node(
                Node(
                    id=csr_id,
                    type=NodeType.CSR,
                    region=preagg_region,
                    vendor=vendor,
                    site_id=site_id,
                    lat=lat,
                    lon=lon,
                )
            )
            is_microwave = rng.random() < tcfg["microwave_fraction"]
            link_type = "microwave" if is_microwave else "fiber"
            capacity = (
                rng.choice(MICROWAVE_CAPACITY_MBPS)
                if is_microwave
                else FIBER_CAPACITY_BY_LAYER["access"]
            )
            # cell sites are single-homed: no redundant backhaul path
            add_link(preagg, csr_id, link_type, capacity, redundant=False)

            gnb_id = f"gnb-{site_counter}"
            add_node(
                Node(
                    id=gnb_id,
                    type=NodeType.GNB,
                    region=preagg_region,
                    vendor=vendor,
                    site_id=site_id,
                    lat=lat,
                    lon=lon,
                )
            )
            add_link(csr_id, gnb_id, "internal", 10_000, redundant=False)

            for c in range(tcfg["cells_per_site"]):
                cell_id = f"cell-{site_counter}-{c + 1}"
                add_node(
                    Node(
                        id=cell_id,
                        type=NodeType.CELL,
                        region=preagg_region,
                        vendor=vendor,
                        site_id=site_id,
                        lat=lat,
                        lon=lon,
                    )
                )
                add_link(gnb_id, cell_id, "internal", 1_000, redundant=False)

            power_id = f"power-{site_counter}"
            add_node(
                Node(
                    id=power_id,
                    type=NodeType.POWER,
                    region=preagg_region,
                    vendor=vendor,
                    site_id=site_id,
                    lat=lat,
                    lon=lon,
                )
            )
            add_link(csr_id, power_id, "internal", 0, redundant=False)

            sync_id = f"sync-{site_counter}"
            add_node(
                Node(
                    id=sync_id,
                    type=NodeType.SYNC,
                    region=preagg_region,
                    vendor=vendor,
                    site_id=site_id,
                    lat=lat,
                    lon=lon,
                )
            )
            add_link(csr_id, sync_id, "internal", 0, redundant=False)

    return list(nodes.values()), links, graph
