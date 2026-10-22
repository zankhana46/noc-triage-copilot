"""Loads the generated topology CSVs into Neo4j as a property graph:
(:Node {id, type, region, vendor, site_id, lat, lon})
      -[:CONNECTS_TO {link_type, capacity_mbps, redundant}]->
(:Node)
Edges point parent -> child (upstream -> downstream), matching the
networkx.DiGraph built in simulator/topology.py.
"""

from __future__ import annotations

import csv
from pathlib import Path

from neo4j import Driver, GraphDatabase


def get_driver(uri: str, user: str, password: str) -> Driver:
    return GraphDatabase.driver(uri, auth=(user, password))


def clear_graph(driver: Driver) -> None:
    with driver.session() as session:
        session.run("MATCH (n:Node) DETACH DELETE n")


def ensure_constraints(driver: Driver) -> None:
    with driver.session() as session:
        session.run(
            "CREATE CONSTRAINT node_id_unique IF NOT EXISTS FOR (n:Node) REQUIRE n.id IS UNIQUE"
        )


def _read_csv(path: Path) -> list[dict]:
    with open(path, newline="") as f:
        return list(csv.DictReader(f))


def load_topology(
    driver: Driver, nodes_csv: Path, links_csv: Path, batch_size: int = 500
) -> dict[str, int]:
    ensure_constraints(driver)
    nodes = _read_csv(nodes_csv)
    links = _read_csv(links_csv)

    with driver.session() as session:
        for i in range(0, len(nodes), batch_size):
            batch = nodes[i : i + batch_size]
            session.run(
                """
                UNWIND $rows AS row
                MERGE (n:Node {id: row.id})
                SET n.type = row.type,
                    n.region = row.region,
                    n.vendor = row.vendor,
                    n.site_id = row.site_id,
                    n.lat = toFloat(row.lat),
                    n.lon = toFloat(row.lon)
                """,
                rows=batch,
            )
        for i in range(0, len(links), batch_size):
            batch = links[i : i + batch_size]
            session.run(
                """
                UNWIND $rows AS row
                MATCH (a:Node {id: row.source})
                MATCH (b:Node {id: row.target})
                MERGE (a)-[r:CONNECTS_TO]->(b)
                SET r.link_type = row.link_type,
                    r.capacity_mbps = toInteger(row.capacity_mbps),
                    r.redundant = (row.redundant = 'True')
                """,
                rows=batch,
            )
    return {"nodes": len(nodes), "links": len(links)}
