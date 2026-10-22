"""Builds the Phase 4 QLoRA fine-tuning dataset: the input is the exact
alarm + topology evidence the live Diagnostician and Remediation Writer
nodes see, the target is the ground-truth structured output. Built
entirely from the simulator's planted ground truth, so no LLM calls (and
no GPU) are needed to build it — only Neo4j, for the same graph-tool
evidence the live Topology Analyst would gather.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from neo4j import Driver

from noc_copilot.agents.correlator import make_alarm_correlator_node
from noc_copilot.agents.diagnostician import SYSTEM_PROMPT as DIAGNOSTICIAN_SYSTEM_PROMPT
from noc_copilot.agents.diagnostician import format_alarms, format_candidates
from noc_copilot.agents.pipeline import strip_ground_truth
from noc_copilot.agents.remediation_writer import SYSTEM_PROMPT as REMEDIATION_SYSTEM_PROMPT
from noc_copilot.agents.remediation_writer import format_chunks
from noc_copilot.agents.state import Citation, DiagnosticianOutput, Hypothesis, RemediationOutput
from noc_copilot.agents.topology_analyst import make_topology_analyst_node
from noc_copilot.retrieval.chunking import RunbookChunk, load_runbook_chunks
from noc_copilot.simulator.schema import IncidentBundle

_BULLET_RE = re.compile(r"^[*-]\s+\*\*(.+?)\*\*:?\s*(.*)$")
MAX_AFFECTED_NODES_IN_SUMMARY = 5


def _extract_remediation_steps(chunk_text: str) -> list[str]:
    """Pulls each top-level bullet out of a Remediation section's Markdown
    (skipping nested sub-bullets) as one remediation step string."""
    steps = []
    for line in chunk_text.splitlines():
        if line.startswith("  "):  # nested sub-bullet, not a top-level step
            continue
        stripped = line.strip()
        match = _BULLET_RE.match(stripped)
        if match:
            head, rest = match.groups()
            head = head.rstrip(":").strip()  # the bold span may include its own trailing colon
            steps.append(f"{head}: {rest}".rstrip(": ").strip())
        elif stripped.startswith(("* ", "- ")):
            steps.append(stripped.lstrip("*- ").strip())
    return steps


def _diagnostician_example(
    bundle: IncidentBundle,
    correlated_alarms: list[dict],
    candidates: list,
    evidence_path: list[dict],
) -> dict[str, Any]:
    candidates_section = format_candidates(candidates, evidence_path)
    user_content = f"Alarm evidence:\n{format_alarms(correlated_alarms)}\n\n{candidates_section}"
    meaning = bundle.failure_type.value.replace("_", " ")
    rationale = (
        f"The alarm evidence and topology candidates above are consistent with "
        f"{meaning} at {bundle.root_cause_node}."
    )
    target = DiagnosticianOutput(
        hypotheses=[
            Hypothesis(
                root_cause_type=bundle.failure_type.value,
                root_cause_node=bundle.root_cause_node,
                confidence=1.0,
                rationale=rationale,
            )
        ],
        needs_more_evidence=False,
    )
    return {
        "messages": [
            {"role": "system", "content": DIAGNOSTICIAN_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": target.model_dump_json()},
        ]
    }


def _remediation_example(
    bundle: IncidentBundle,
    correlated_alarms: list[dict],
    correlated_node_ids: list[str],
    runbook_chunks: list[RunbookChunk],
) -> dict[str, Any] | None:
    remediation_chunk = next((c for c in runbook_chunks if c.section == "Remediation"), None)
    if remediation_chunk is None:
        return None
    steps = _extract_remediation_steps(remediation_chunk.text)
    if not steps:
        return None

    meaning = bundle.failure_type.value.replace("_", " ")
    shown = bundle.affected_nodes[:MAX_AFFECTED_NODES_IN_SUMMARY]
    more = "..." if len(bundle.affected_nodes) > MAX_AFFECTED_NODES_IN_SUMMARY else ""
    incident_summary = (
        f"Incident diagnosed as {meaning} at {bundle.root_cause_node}, affecting "
        f"{len(bundle.affected_nodes)} node(s): {', '.join(shown)}{more}."
    )
    target = RemediationOutput(
        incident_summary=incident_summary,
        remediation_steps=steps,
        citations=[
            Citation(
                source=f"runbook:{remediation_chunk.chunk_id}",
                detail=f"Remediation steps for {meaning}.",
            ),
            Citation(
                source=f"topology:{bundle.root_cause_node}",
                detail=f"Root cause node ({bundle.failure_type.value}).",
            ),
        ],
    )

    alarm_summary = "; ".join(f"{a['node_id']}:{a['alarm_type']}" for a in correlated_alarms[:10])
    user_content = (
        f"Diagnosed root cause: type={bundle.failure_type.value}, node={bundle.root_cause_node}, "
        f"confidence=1.0. Rationale: ground truth.\n\n"
        f"Alarm evidence (node:alarm_type): {alarm_summary or '(none)'}\n\n"
        f"Retrieved runbook sections:\n{format_chunks([c.model_dump() for c in runbook_chunks])}"
    )
    return {
        "messages": [
            {"role": "system", "content": REMEDIATION_SYSTEM_PROMPT},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": target.model_dump_json()},
        ]
    }


def build_examples_for_bundle(
    bundle: IncidentBundle,
    driver: Driver,
    runbook_chunks_by_id: dict[str, list[RunbookChunk]],
) -> list[dict[str, Any]]:
    """One Diagnostician-style and (if the runbook has a Remediation
    section) one Remediation-Writer-style training example per bundle."""
    alarm_dicts = strip_ground_truth([a.model_dump() for a in bundle.alarms])
    correlator_fn = make_alarm_correlator_node(driver)
    topology_fn = make_topology_analyst_node(driver)

    correlator_update = correlator_fn({"alarms": alarm_dicts, "use_graph_tools": True})
    # merging via ** loses the TypedDict's structural type for mypy; the
    # keys are correct at runtime (verified by tests/test_finetune.py)
    topology_state: Any = {**correlator_update, "use_graph_tools": True, "loop_count": 0}
    topology_update = topology_fn(topology_state)

    examples = [
        _diagnostician_example(
            bundle,
            correlator_update["correlated_alarms"],
            topology_update["root_cause_candidates"],
            topology_update["evidence_path"],
        )
    ]

    runbook_chunks = runbook_chunks_by_id.get(bundle.runbook_id, [])
    remediation_example = _remediation_example(
        bundle,
        correlator_update["correlated_alarms"],
        correlator_update["correlated_node_ids"],
        runbook_chunks,
    )
    if remediation_example is not None:
        examples.append(remediation_example)

    return examples


def load_runbook_chunks_by_id(runbooks_dir: Path) -> dict[str, list[RunbookChunk]]:
    by_runbook: dict[str, list[RunbookChunk]] = {}
    for chunk in load_runbook_chunks(runbooks_dir):
        by_runbook.setdefault(chunk.runbook_id, []).append(chunk)
    return by_runbook
