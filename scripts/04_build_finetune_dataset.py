#!/usr/bin/env python3
"""Phase 4: builds the QLoRA fine-tuning dataset from the train split
(and a capped dev set from the dev split for training-time eval loss).
Runs entirely locally — no GPU, no LLM calls — since every target is
built from the simulator's planted ground truth plus the same graph-tool
evidence the live Topology Analyst gathers.

Writes data/finetune/train.jsonl and data/finetune/dev.jsonl (chat format:
one {"messages": [...]} object per line).

Usage: uv run --extra dev scripts/04_build_finetune_dataset.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import yaml
from dotenv import load_dotenv

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))

from noc_copilot.eval.retrieval_eval import load_bundles  # noqa: E402
from noc_copilot.finetune.dataset_builder import (  # noqa: E402
    build_examples_for_bundle,
    load_runbook_chunks_by_id,
)
from noc_copilot.graph.loader import get_driver  # noqa: E402


def build_split(bundles, driver, runbook_chunks_by_id, out_path: Path) -> int:
    n_examples = 0
    with open(out_path, "w") as f:
        for bundle in bundles:
            for example in build_examples_for_bundle(bundle, driver, runbook_chunks_by_id):
                f.write(json.dumps(example) + "\n")
                n_examples += 1
    return n_examples


def main() -> None:
    load_dotenv(REPO_ROOT / ".env")
    cfg = yaml.safe_load((REPO_ROOT / "configs" / "finetune.yaml").read_text())
    dcfg = cfg["dataset"]

    out_dir = REPO_ROOT / dcfg["out_dir"]
    out_dir.mkdir(parents=True, exist_ok=True)

    driver = get_driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ["NEO4J_PASSWORD"],
    )
    runbook_chunks_by_id = load_runbook_chunks_by_id(REPO_ROOT / "data" / "runbooks")

    train_bundles = load_bundles(REPO_ROOT / dcfg["train_bundles"])
    print(f"Building train set from {len(train_bundles)} bundles...")
    n_train = build_split(train_bundles, driver, runbook_chunks_by_id, out_dir / "train.jsonl")
    print(f"  {n_train} examples -> {out_dir / 'train.jsonl'}")

    dev_bundles = load_bundles(REPO_ROOT / dcfg["dev_bundles"])[: dcfg["max_dev_examples"]]
    print(f"Building dev set from {len(dev_bundles)} bundles...")
    n_dev = build_split(dev_bundles, driver, runbook_chunks_by_id, out_dir / "dev.jsonl")
    print(f"  {n_dev} examples -> {out_dir / 'dev.jsonl'}")

    driver.close()


if __name__ == "__main__":
    main()
