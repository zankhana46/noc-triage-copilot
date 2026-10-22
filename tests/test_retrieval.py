"""Tests for runbook chunking (the retrieval index's input)."""

from __future__ import annotations

from noc_copilot.retrieval.chunking import chunk_runbook

SAMPLE_RUNBOOK = """\
# Fiber Cut Runbook

## Symptoms
- Link-down alarm on an aggregation fiber interface.
- Downstream sites report S1/NG failures.

## Probable causes
- Physical fiber cut.

## Diagnostic steps
- Check optical power levels.

## Remediation
- Dispatch a field technician.

## Escalation
- Escalate to Tier 2 transport team.
"""


def test_chunk_runbook_splits_every_section():
    chunks = chunk_runbook("fiber_cut", SAMPLE_RUNBOOK)
    sections = [c.section for c in chunks]
    assert sections == [
        "Symptoms", "Probable causes", "Diagnostic steps", "Remediation", "Escalation",
    ]


def test_chunk_runbook_excludes_the_title_line():
    chunks = chunk_runbook("fiber_cut", SAMPLE_RUNBOOK)
    assert not any("Fiber Cut Runbook" in c.text for c in chunks)


def test_chunk_runbook_keeps_full_section_body_not_just_the_header():
    chunks = chunk_runbook("fiber_cut", SAMPLE_RUNBOOK)
    symptoms = next(c for c in chunks if c.section == "Symptoms")
    assert "Link-down alarm" in symptoms.text
    assert "Downstream sites report S1/NG failures" in symptoms.text


def test_chunk_ids_are_unique_and_scoped_to_runbook():
    chunks = chunk_runbook("fiber_cut", SAMPLE_RUNBOOK)
    assert len({c.chunk_id for c in chunks}) == len(chunks)
    assert all(c.chunk_id.startswith("fiber_cut::") for c in chunks)
