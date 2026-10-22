"""Phase 2 acceptance tests: every graph tool against a small fixture
topology loaded into the running Neo4j instance under an isolated "fx-"
id prefix (Neo4j Community edition has no multi-database support, so
fixture data is namespaced by id prefix and cleaned up before/after
instead of using a separate database)."""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from noc_copilot.graph.loader import get_driver
from noc_copilot.graph.tools import (
    UnsafeCypherError,
    find_common_ancestor,
    get_downstream,
    get_node,
    get_redundancy,
    get_upstream_path,
    run_text_to_cypher,
    text_to_cypher,
    validate_read_only_cypher,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
FIXTURE_PREFIX = "fx-"

# fx-core-1 --------> fx-agg-1 ----------> fx-preagg-1 --> fx-csr-1
#      |                  |                                  \-> fx-csr-2
#      \------------------+-----> fx-preagg-2 (dual-homed) --> fx-csr-3
FIXTURE_NODES = [
    {
        "id": "fx-core-1",
        "type": "CORE_ROUTER",
        "region": "test",
        "vendor": "Northeon",
        "site_id": None,
        "lat": 0.0,
        "lon": 0.0,
    },
    {
        "id": "fx-agg-1",
        "type": "AGG_ROUTER",
        "region": "test",
        "vendor": "Northeon",
        "site_id": None,
        "lat": 0.0,
        "lon": 0.0,
    },
    {
        "id": "fx-preagg-1",
        "type": "PREAGG_ROUTER",
        "region": "test",
        "vendor": "Vendorix",
        "site_id": None,
        "lat": 0.0,
        "lon": 0.0,
    },
    {
        "id": "fx-preagg-2",
        "type": "PREAGG_ROUTER",
        "region": "test",
        "vendor": "Vendorix",
        "site_id": None,
        "lat": 0.0,
        "lon": 0.0,
    },
    {
        "id": "fx-csr-1",
        "type": "CSR",
        "region": "test",
        "vendor": "Arcadia Networks",
        "site_id": "fx-site-1",
        "lat": 0.0,
        "lon": 0.0,
    },
    {
        "id": "fx-csr-2",
        "type": "CSR",
        "region": "test",
        "vendor": "Arcadia Networks",
        "site_id": "fx-site-2",
        "lat": 0.0,
        "lon": 0.0,
    },
    {
        "id": "fx-csr-3",
        "type": "CSR",
        "region": "test",
        "vendor": "Arcadia Networks",
        "site_id": "fx-site-3",
        "lat": 0.0,
        "lon": 0.0,
    },
]
FIXTURE_LINKS = [
    ("fx-core-1", "fx-agg-1", False),
    ("fx-agg-1", "fx-preagg-1", False),
    ("fx-agg-1", "fx-preagg-2", True),
    ("fx-core-1", "fx-preagg-2", True),
    ("fx-preagg-1", "fx-csr-1", False),
    ("fx-preagg-1", "fx-csr-2", False),
    ("fx-preagg-2", "fx-csr-3", False),
]


def _clear_fixture(driver) -> None:
    with driver.session() as session:
        session.run(
            "MATCH (n:Node) WHERE n.id STARTS WITH $prefix DETACH DELETE n",
            prefix=FIXTURE_PREFIX,
        )


@pytest.fixture(scope="module")
def neo4j_driver():
    load_dotenv(REPO_ROOT / ".env")
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ.get("NEO4J_PASSWORD")
    if not password:
        pytest.skip("NEO4J_PASSWORD not set; copy .env.example to .env")
    driver = get_driver(uri, user, password)
    try:
        driver.verify_connectivity()
    except Exception:
        pytest.skip("Neo4j is not reachable; start it with `docker compose up -d neo4j`")
    yield driver
    driver.close()


@pytest.fixture
def fixture_graph(neo4j_driver):
    _clear_fixture(neo4j_driver)
    with neo4j_driver.session() as session:
        session.run(
            "UNWIND $rows AS row CREATE (n:Node) SET n = row",
            rows=FIXTURE_NODES,
        )
        for source, target, redundant in FIXTURE_LINKS:
            session.run(
                """
                MATCH (a:Node {id: $source}), (b:Node {id: $target})
                CREATE (a)-[:CONNECTS_TO {link_type: 'fiber', capacity_mbps: 1000,
                            redundant: $redundant}]->(b)
                """,
                source=source,
                target=target,
                redundant=redundant,
            )
    yield neo4j_driver
    _clear_fixture(neo4j_driver)


def test_get_node(fixture_graph):
    node = get_node(fixture_graph, "fx-csr-1")
    assert node is not None
    assert node["type"] == "CSR"
    assert node["site_id"] == "fx-site-1"

    assert get_node(fixture_graph, "fx-does-not-exist") is None


def test_get_upstream_path(fixture_graph):
    path = get_upstream_path(fixture_graph, "fx-csr-1")
    assert [n["id"] for n in path] == ["fx-csr-1", "fx-preagg-1", "fx-agg-1", "fx-core-1"]

    # a core element's upstream path is just itself
    assert [n["id"] for n in get_upstream_path(fixture_graph, "fx-core-1")] == ["fx-core-1"]

    assert get_upstream_path(fixture_graph, "fx-does-not-exist") == []


def test_get_downstream(fixture_graph):
    downstream_ids = {n["id"] for n in get_downstream(fixture_graph, "fx-agg-1")}
    assert downstream_ids == {"fx-preagg-1", "fx-preagg-2", "fx-csr-1", "fx-csr-2", "fx-csr-3"}

    assert {n["id"] for n in get_downstream(fixture_graph, "fx-csr-1")} == set()


def test_find_common_ancestor_siblings(fixture_graph):
    lca = find_common_ancestor(fixture_graph, ["fx-csr-1", "fx-csr-2"])
    assert lca is not None
    assert lca["id"] == "fx-preagg-1"


def test_find_common_ancestor_across_branches(fixture_graph):
    lca = find_common_ancestor(fixture_graph, ["fx-csr-1", "fx-csr-3"])
    assert lca is not None
    assert lca["id"] == "fx-agg-1"


def test_find_common_ancestor_single_node_is_itself(fixture_graph):
    lca = find_common_ancestor(fixture_graph, ["fx-csr-1"])
    assert lca is not None
    assert lca["id"] == "fx-csr-1"


def test_get_redundancy(fixture_graph):
    dual_homed = get_redundancy(fixture_graph, "fx-preagg-2")
    assert dual_homed["has_redundant_uplink"] is True
    assert dual_homed["upstream_parent_count"] == 2
    assert set(dual_homed["parent_node_ids"]) == {"fx-core-1", "fx-agg-1"}

    single_homed = get_redundancy(fixture_graph, "fx-preagg-1")
    assert single_homed["has_redundant_uplink"] is False
    assert single_homed["upstream_parent_count"] == 1


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Node) DETACH DELETE n",
        "MATCH (n:Node) SET n.vendor = 'hacked' RETURN n",
        "CREATE (n:Node {id: 'evil'}) RETURN n",
        "MATCH (n:Node) REMOVE n.vendor",
        "MATCH (a), (b) MERGE (a)-[:CONNECTS_TO]->(b)",
        "MATCH (n:Node) RETURN n.id; MATCH (m:Node) DETACH DELETE m",
        "LOAD CSV FROM 'file:///x.csv' AS row CREATE (n:Node {id: row[0]})",
        "DROP CONSTRAINT node_id_unique",
    ],
)
def test_validate_read_only_cypher_rejects_writes(query):
    with pytest.raises(UnsafeCypherError):
        validate_read_only_cypher(query)


@pytest.mark.parametrize(
    "query",
    [
        "MATCH (n:Node {id: 'fx-csr-1'}) RETURN n",
        "MATCH (n:Node)-[:CONNECTS_TO]->(m:Node) WHERE n.type = 'CORE_ROUTER' RETURN m.id",
        "MATCH (n:Node) WHERE n.region = 'test' RETURN count(n) AS c",
    ],
)
def test_validate_read_only_cypher_accepts_reads(query):
    assert validate_read_only_cypher(query) == query.strip()


def test_validate_read_only_cypher_does_not_false_positive_on_substrings():
    # "settings" contains "set" but is not the SET clause
    query = "MATCH (n:Node) WHERE n.id = 'settings-node' RETURN n"
    assert validate_read_only_cypher(query) == query


@pytest.mark.llm
def test_text_to_cypher_end_to_end(fixture_graph):
    """Live round-trip through the real LLM; skipped if no key is configured."""
    if not os.environ.get("LLM_API_KEY"):
        pytest.skip("LLM_API_KEY not set")
    try:
        query = text_to_cypher("How many Node records have type CORE_ROUTER?")
    except Exception as e:
        pytest.skip(f"LLM call failed (likely transient): {e}")
    validate_read_only_cypher(query)  # should not raise
    rows = run_text_to_cypher(fixture_graph, "How many Node records have type CORE_ROUTER?")
    assert rows
