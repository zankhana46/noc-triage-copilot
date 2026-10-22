"""Tests for the Phase 4 fine-tuning dataset builder."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from dotenv import load_dotenv

from noc_copilot.agents.state import DiagnosticianOutput, RemediationOutput
from noc_copilot.eval.retrieval_eval import load_bundles
from noc_copilot.finetune.dataset_builder import (
    _extract_remediation_steps,
    build_examples_for_bundle,
    load_runbook_chunks_by_id,
)
from noc_copilot.graph.loader import get_driver

REPO_ROOT = Path(__file__).resolve().parents[1]

SAMPLE_REMEDIATION_MARKDOWN = """\
## Remediation

* **Enable additional carrier/sector capacity if available:** Remotely activate soft-locked \
carrier licenses, bring dormant carriers online, or enable secondary Carrier Aggregation (CA) \
layers on the cell.
* **Apply traffic steering to neighboring cells with spare capacity:**
  * Adjust idle-mode cell reselection parameters to prioritize underutilized adjacent frequencies.
  * Tweak handover margins to offload edge subscribers.
* **Queue a capacity upgrade request if growth is sustained:** Submit a formal capacity \
augmentation request via the ticketing system.
"""


def test_extract_remediation_steps_no_double_colon():
    steps = _extract_remediation_steps(SAMPLE_REMEDIATION_MARKDOWN)
    assert len(steps) == 3
    assert "::" not in steps[0]
    assert steps[0].startswith("Enable additional carrier/sector capacity if available:")


def test_extract_remediation_steps_skips_nested_bullets():
    steps = _extract_remediation_steps(SAMPLE_REMEDIATION_MARKDOWN)
    assert steps[1] == "Apply traffic steering to neighboring cells with spare capacity"
    assert not any("idle-mode" in s for s in steps)  # nested sub-bullet content excluded


def test_extract_remediation_steps_empty_text():
    assert _extract_remediation_steps("") == []
    assert _extract_remediation_steps("## Remediation\n\nNo bullets here.") == []


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


@pytest.fixture(scope="module")
def runbook_chunks_by_id():
    runbooks_dir = REPO_ROOT / "data" / "runbooks"
    if not runbooks_dir.exists() or not any(runbooks_dir.glob("*.md")):
        pytest.skip("data/runbooks/ not found; run scripts/01_generate_data.py first")
    return load_runbook_chunks_by_id(runbooks_dir)


@pytest.fixture(scope="module")
def a_train_bundle():
    path = REPO_ROOT / "data" / "incidents" / "train.jsonl"
    if not path.exists():
        pytest.skip("data/incidents/train.jsonl not found; run scripts/01_generate_data.py first")
    return load_bundles(path)[0]


def test_build_examples_for_bundle_shape(neo4j_driver, runbook_chunks_by_id, a_train_bundle):
    examples = build_examples_for_bundle(a_train_bundle, neo4j_driver, runbook_chunks_by_id)
    assert 1 <= len(examples) <= 2
    for example in examples:
        roles = [m["role"] for m in example["messages"]]
        assert roles == ["system", "user", "assistant"]
        json.loads(example["messages"][-1]["content"])  # must be valid JSON


def test_diagnostician_example_matches_ground_truth(
    neo4j_driver, runbook_chunks_by_id, a_train_bundle
):
    examples = build_examples_for_bundle(a_train_bundle, neo4j_driver, runbook_chunks_by_id)
    diagnostician_example = examples[0]
    target = DiagnosticianOutput.model_validate_json(
        diagnostician_example["messages"][-1]["content"]
    )
    assert target.hypotheses[0].root_cause_type == a_train_bundle.failure_type.value
    assert target.hypotheses[0].root_cause_node == a_train_bundle.root_cause_node
    assert target.hypotheses[0].confidence == 1.0
    assert target.needs_more_evidence is False


def test_remediation_example_is_grounded_in_the_correct_runbook(
    neo4j_driver, runbook_chunks_by_id, a_train_bundle
):
    examples = build_examples_for_bundle(a_train_bundle, neo4j_driver, runbook_chunks_by_id)
    if len(examples) < 2:
        pytest.skip("this bundle's runbook has no Remediation section")
    remediation_example = examples[1]
    target = RemediationOutput.model_validate_json(remediation_example["messages"][-1]["content"])
    assert target.remediation_steps
    assert any(a_train_bundle.root_cause_node in c.source for c in target.citations)
    assert any(a_train_bundle.runbook_id in c.source for c in target.citations)
