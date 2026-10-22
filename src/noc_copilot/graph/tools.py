"""Safe, parameterized Cypher tools used by the LangGraph agents (Phase 3)
to reason about the network topology stored in Neo4j.
"""

from __future__ import annotations

import re

from neo4j import Driver
from openai.types.chat import ChatCompletionMessageParam

from noc_copilot.llm.client import chat

CORE_TYPES = ["CORE_ROUTER", "AMF", "UPF"]

SCHEMA_DESCRIPTION = """\
Node label: :Node
Node properties: id (string, unique), type (one of AMF, UPF, CORE_ROUTER, AGG_ROUTER,
PREAGG_ROUTER, CSR, GNB, CELL, POWER, SYNC), region (string), vendor (string),
site_id (string or null — shared by all equipment at the same cell site), lat (float), lon (float)

Relationship: (:Node)-[:CONNECTS_TO]->(:Node), directed from the upstream/parent node
(closer to the core) to the downstream/child node (closer to the edge).
Relationship properties: link_type ("fiber" | "microwave" | "internal"),
capacity_mbps (integer), redundant (boolean — true if this link is one of two or more
parallel upstream paths into the target node).

Core node types (no node above them except each other): CORE_ROUTER, AMF, UPF.
"""


def get_node(driver: Driver, node_id: str) -> dict | None:
    with driver.session() as session:
        record = session.execute_read(
            lambda tx: tx.run("MATCH (n:Node {id: $id}) RETURN n", id=node_id).single()
        )
        return dict(record["n"]) if record else None


def get_upstream_path(driver: Driver, node_id: str) -> list[dict]:
    """Shortest path from the node up to the nearest core element, returned
    ordered [node, ..., core]. Empty list if the node doesn't exist or has
    no path to the core (e.g. it IS a core element, in which case the
    single-element list [node] is returned)."""
    with driver.session() as session:
        node = get_node(driver, node_id)
        if node is None:
            return []
        if node["type"] in CORE_TYPES:
            return [node]

        def _query(tx):
            return tx.run(
                """
                MATCH p = (core:Node)-[:CONNECTS_TO*1..20]->(n:Node {id: $id})
                WHERE core.type IN $core_types
                RETURN [node IN nodes(p) | node] AS path_nodes
                ORDER BY length(p) ASC
                LIMIT 1
                """,
                id=node_id,
                core_types=CORE_TYPES,
            ).single()

        record = session.execute_read(_query)
        if record is None:
            return []
        path_nodes = [dict(n) for n in record["path_nodes"]]
        return list(reversed(path_nodes))  # node -> ... -> core


def get_downstream(driver: Driver, node_id: str) -> list[dict]:
    """Everything served by (downstream of) the given node."""
    with driver.session() as session:
        result = session.execute_read(
            lambda tx: list(
                tx.run(
                    "MATCH (n:Node {id: $id})-[:CONNECTS_TO*]->(d:Node) RETURN DISTINCT d",
                    id=node_id,
                )
            )
        )
        return [dict(r["d"]) for r in result]


def _ancestor_ids_including_self(driver: Driver, node_id: str) -> set[str]:
    with driver.session() as session:
        result = session.execute_read(
            lambda tx: list(
                tx.run(
                    "MATCH (a:Node)-[:CONNECTS_TO*0..]->(n:Node {id: $id}) "
                    "RETURN DISTINCT a.id AS id",
                    id=node_id,
                )
            )
        )
        return {r["id"] for r in result}


def _depth_from_core(driver: Driver, node_id: str) -> int:
    """Hop count from the nearest core element; 0 if the node is itself a
    core element. Used to pick the *lowest* (most specific) common
    ancestor among several candidates."""
    with driver.session() as session:
        record = session.execute_read(
            lambda tx: tx.run(
                """
                MATCH p = (core:Node)-[:CONNECTS_TO*0..20]->(n:Node {id: $id})
                WHERE core.type IN $core_types
                RETURN min(length(p)) AS depth
                """,
                id=node_id,
                core_types=CORE_TYPES,
            ).single()
        )
        return record["depth"] if record and record["depth"] is not None else 0


def find_common_ancestor(driver: Driver, node_ids: list[str]) -> dict | None:
    """Lowest shared upstream node for a set of alarming nodes — the key
    RCA signal. Returns None if the nodes share no common ancestor (should
    not happen in this topology, where every node traces back to a core
    element)."""
    if not node_ids:
        return None
    ancestor_sets = [_ancestor_ids_including_self(driver, nid) for nid in node_ids]
    common = set.intersection(*ancestor_sets)
    if not common:
        return None
    depths = {cid: _depth_from_core(driver, cid) for cid in common}
    lca_id = max(depths, key=lambda cid: depths[cid])
    return get_node(driver, lca_id)


def get_redundancy(driver: Driver, node_id: str) -> dict:
    """Whether the node has more than one upstream parent — i.e. losing a
    single uplink still leaves it connected to the core."""
    with driver.session() as session:
        parent_ids = session.execute_read(
            lambda tx: [
                r["p"]
                for r in tx.run(
                    "MATCH (p:Node)-[:CONNECTS_TO]->(n:Node {id: $id}) RETURN p.id AS p",
                    id=node_id,
                )
            ]
        )
    return {
        "node_id": node_id,
        "has_redundant_uplink": len(parent_ids) > 1,
        "upstream_parent_count": len(parent_ids),
        "parent_node_ids": parent_ids,
    }


def are_adjacent(driver: Driver, node_id_a: str, node_id_b: str) -> bool:
    """True if there is a direct CONNECTS_TO edge between the two nodes, in
    either direction. Used for "localized within one hop" eval metrics."""
    if node_id_a == node_id_b:
        return True
    with driver.session() as session:
        record = session.execute_read(
            lambda tx: tx.run(
                "MATCH (a:Node {id: $a})-[:CONNECTS_TO]-(b:Node {id: $b}) "
                "RETURN count(*) > 0 AS adjacent",
                a=node_id_a,
                b=node_id_b,
            ).single()
        )
        return bool(record["adjacent"]) if record else False


# --- text_to_cypher: LLM-generated, validated read-only Cypher ---

_FORBIDDEN_WORDS = {"CREATE", "MERGE", "DELETE", "DETACH", "SET", "REMOVE", "DROP", "FOREACH"}
_FORBIDDEN_PHRASES = ["LOAD CSV", "CALL {"]  # subqueries can smuggle writes
_CODE_FENCE_RE = re.compile(r"^```(?:cypher)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


class UnsafeCypherError(ValueError):
    pass


def validate_read_only_cypher(query: str) -> str:
    """Raises UnsafeCypherError if the query contains any write clause or
    multiple statements; returns the (trimmed) query otherwise."""
    cleaned = query.strip()
    if not cleaned:
        raise UnsafeCypherError("Empty query")
    if ";" in cleaned.rstrip(";"):
        raise UnsafeCypherError("Only a single Cypher statement is allowed")
    normalized = cleaned.upper()
    for phrase in _FORBIDDEN_PHRASES:
        if phrase in normalized:
            raise UnsafeCypherError(f"Query contains a forbidden clause: {phrase}")
    tokens = set(re.findall(r"[A-Z_]+", normalized))
    hit = tokens & _FORBIDDEN_WORDS
    if hit:
        raise UnsafeCypherError(f"Query contains a forbidden write clause: {', '.join(hit)}")
    return cleaned


def text_to_cypher(question: str) -> str:
    """LLM-generated Cypher for `question`, validated as read-only before
    being returned. Raises UnsafeCypherError if the model produces a write
    query despite instructions."""
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": (
                "You are a Cypher query generator for a telecom network topology graph "
                "in Neo4j. Given the schema and a question, write ONE read-only Cypher "
                "query (MATCH / OPTIONAL MATCH / WITH / UNWIND / RETURN only — never "
                "CREATE, MERGE, DELETE, SET, REMOVE, DROP, LOAD CSV, or FOREACH) that "
                "answers the question. Reply with ONLY the Cypher query: no explanation, "
                "no markdown code fences.\n\n" + SCHEMA_DESCRIPTION
            ),
        },
        {"role": "user", "content": question},
    ]
    raw = chat(messages, temperature=0.0)
    query = _CODE_FENCE_RE.sub("", raw).strip()
    return validate_read_only_cypher(query)


def run_text_to_cypher(driver: Driver, question: str) -> list[dict]:
    """Generates, validates, and executes a read-only Cypher query for
    `question`. Uses Neo4j's own read-transaction access mode as a second,
    server-enforced line of defense beyond validate_read_only_cypher."""
    query = text_to_cypher(question)
    with driver.session() as session:
        result = session.execute_read(lambda tx: list(tx.run(query)))
        return [dict(r) for r in result]
