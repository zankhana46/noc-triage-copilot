#!/usr/bin/env python3
"""Phase 1: load data/topology/{nodes,links}.csv into Neo4j.

Usage: uv run scripts/01_load_graph.py
Requires `docker compose up -d neo4j` to be running and NEO4J_* in .env.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from noc_copilot.graph.loader import get_driver, load_topology  # noqa: E402


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    uri = os.environ.get("NEO4J_URI", "bolt://localhost:7687")
    user = os.environ.get("NEO4J_USER", "neo4j")
    password = os.environ["NEO4J_PASSWORD"]

    nodes_csv = REPO_ROOT / "data" / "topology" / "nodes.csv"
    links_csv = REPO_ROOT / "data" / "topology" / "links.csv"
    if not nodes_csv.exists() or not links_csv.exists():
        raise SystemExit("Run scripts/01_generate_data.py first to produce data/topology/*.csv")

    driver = get_driver(uri, user, password)
    try:
        driver.verify_connectivity()
        counts = load_topology(driver, nodes_csv, links_csv)
        print(f"Loaded {counts['nodes']} nodes and {counts['links']} links into Neo4j at {uri}")
    finally:
        driver.close()


if __name__ == "__main__":
    main()
