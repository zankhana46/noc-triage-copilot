#!/usr/bin/env python3
"""Phase 1: generate topology, runbooks, and incident bundles from
configs/simulator.yaml. Writes data/{topology,alarms,runbooks,incidents}/
and results/data_summary.json.

Usage: uv run scripts/01_generate_data.py [--config configs/simulator.yaml]
"""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from noc_copilot.simulator.alarms import build_paraphrase_cache, enable_llm_paraphrasing  # noqa: E402
from noc_copilot.simulator.incidents import generate_incident_bundles  # noqa: E402
from noc_copilot.simulator.runbooks import generate_all_runbooks  # noqa: E402
from noc_copilot.simulator.topology import build_topology  # noqa: E402


def export_topology_csv(nodes, links, topology_dir: Path) -> None:
    topology_dir.mkdir(parents=True, exist_ok=True)
    import csv

    with open(topology_dir / "nodes.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["id", "type", "region", "vendor", "site_id", "lat", "lon"]
        )
        writer.writeheader()
        for n in nodes:
            row = n.model_dump()
            row["type"] = row["type"].value if hasattr(row["type"], "value") else row["type"]
            writer.writerow(row)

    with open(topology_dir / "links.csv", "w", newline="") as f:
        writer = csv.DictWriter(
            f, fieldnames=["source", "target", "link_type", "capacity_mbps", "redundant"]
        )
        writer.writeheader()
        for link in links:
            writer.writerow(link.model_dump())


def write_incident_bundles(bundles, incidents_dir: Path) -> None:
    incidents_dir.mkdir(parents=True, exist_ok=True)
    by_split: dict[str, list] = {"train": [], "dev": [], "test": []}
    for b in bundles:
        by_split[b.split].append(b)
    for split, split_bundles in by_split.items():
        with open(incidents_dir / f"{split}.jsonl", "w") as f:
            for b in split_bundles:
                f.write(b.model_dump_json() + "\n")


def write_all_alarms(bundles, alarms_dir: Path) -> None:
    alarms_dir.mkdir(parents=True, exist_ok=True)
    with open(alarms_dir / "all_alarms.jsonl", "w") as f:
        for b in bundles:
            for alarm in b.alarms:
                f.write(alarm.model_dump_json() + "\n")


def compute_summary(nodes, links, bundles, cfg) -> dict:
    by_failure_type = Counter(b.failure_type.value for b in bundles)
    by_split = Counter(b.split for b in bundles)
    total_alarms = sum(len(b.alarms) for b in bundles)
    noise_alarms = sum(1 for b in bundles for a in b.alarms if a.incident_id is None)
    return {
        "seed": cfg["seed"],
        "topology": {
            "num_nodes": len(nodes),
            "num_links": len(links),
            "node_type_counts": dict(Counter(n.type.value for n in nodes)),
        },
        "incident_bundles": {
            "total": len(bundles),
            "by_failure_type": dict(by_failure_type),
            "by_split": dict(by_split),
        },
        "alarms": {
            "total": total_alarms,
            "noise_count": noise_alarms,
            "noise_ratio": round(noise_alarms / total_alarms, 4) if total_alarms else 0.0,
        },
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--config", default="configs/simulator.yaml", help="path to the simulator config"
    )
    args = parser.parse_args()

    cfg = yaml.safe_load((REPO_ROOT / args.config).read_text())
    rng = random.Random(cfg["seed"])
    paths = {k: REPO_ROOT / v for k, v in cfg["paths"].items()}

    print("Building topology...")
    nodes, links, graph = build_topology(cfg, rng)
    export_topology_csv(nodes, links, paths["topology_dir"])
    print(f"  {len(nodes)} nodes, {len(links)} links -> {paths['topology_dir']}")

    print("Generating runbooks (LLM)...")
    index = generate_all_runbooks(paths["runbooks_dir"])
    print(f"  {len(index)} runbooks -> {paths['runbooks_dir']}")

    if cfg["runbooks"]["llm_paraphrase_alarms"]:
        print("Building alarm message paraphrase cache (LLM)...")
        cache_path = paths["alarms_dir"] / ".cache" / "paraphrase_cache.json"
        cache = build_paraphrase_cache(cache_path, cfg["topology"]["vendors"])
        enable_llm_paraphrasing(cache)
        print(f"  {len(cache)} (vendor, alarm_type) variants -> {cache_path}")

    print(f"Generating {cfg['incidents']['num_bundles']} incident bundles...")
    bundles = generate_incident_bundles(graph, nodes, cfg, rng)
    write_incident_bundles(bundles, paths["incidents_dir"])
    write_all_alarms(bundles, paths["alarms_dir"])
    print(f"  {len(bundles)} bundles -> {paths['incidents_dir']}")

    summary = compute_summary(nodes, links, bundles, cfg)
    paths["results_dir"].mkdir(parents=True, exist_ok=True)
    (paths["results_dir"] / "data_summary.json").write_text(json.dumps(summary, indent=2))
    print(f"Summary written to {paths['results_dir'] / 'data_summary.json'}")
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
