"""Remediation Writer node: produces the final incident report, grounded
only in retrieved runbook text and tool outputs, with citations.
"""

from __future__ import annotations

from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from noc_copilot.agents.state import Hypothesis, RemediationOutput, TriageReport, TriageState
from noc_copilot.llm.client import chat_structured

MAX_ALARMS_IN_PROMPT = 10

SYSTEM_PROMPT = """\
You are the Remediation Writer in a NOC triage pipeline. Write the final incident report.

Base incident_summary and remediation_steps STRICTLY on the retrieved runbook text and the \
alarm/topology evidence given below — do not invent steps that aren't grounded in that \
text. Every remediation step must be traceable to a citation: cite runbook sections by \
their chunk id (e.g. "runbook:fiber_cut::remediation"), or topology/alarm evidence by node \
id (e.g. "topology:csr-12"). If no runbook sections were retrieved, say so in the summary \
and keep remediation_steps to generic, clearly-hedged next steps (escalate to the \
appropriate team), citing only the diagnosis itself.\
"""

_NO_DIAGNOSIS = Hypothesis(
    root_cause_type="unknown", root_cause_node=None, confidence=0.0, rationale="no evidence"
)


def format_chunks(chunks: list[dict]) -> str:
    if not chunks:
        return "(none retrieved)"
    return "\n\n".join(f"[runbook:{c['chunk_id']}] ({c['section']})\n{c['text']}" for c in chunks)


def remediation_writer(state: TriageState) -> dict[str, Any]:
    hypotheses_raw = state.get("hypotheses", [])
    hypotheses = [Hypothesis(**h) for h in hypotheses_raw] or [_NO_DIAGNOSIS]
    top = hypotheses[0]
    chunks = state.get("retrieved_chunks", [])
    affected_nodes = state.get("correlated_node_ids", [])
    confidence = state.get("confidence", 0.0)

    recent_alarms = state.get("correlated_alarms", [])[:MAX_ALARMS_IN_PROMPT]
    alarm_summary = "; ".join(f"{a['node_id']}:{a['alarm_type']}" for a in recent_alarms)
    user_content = (
        f"Diagnosed root cause: type={top.root_cause_type}, node={top.root_cause_node}, "
        f"confidence={top.confidence}. Rationale: {top.rationale}\n\n"
        f"Alarm evidence (node:alarm_type): {alarm_summary or '(none)'}\n\n"
        f"Retrieved runbook sections:\n{format_chunks(chunks)}"
    )
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result, response = chat_structured(messages, RemediationOutput, temperature=0.2)

    report = TriageReport(
        incident_summary=result.incident_summary,
        root_cause_node=top.root_cause_node,
        root_cause_type=top.root_cause_type,
        top3_hypotheses=hypotheses,
        affected_nodes=affected_nodes,
        remediation_steps=result.remediation_steps,
        citations=result.citations,
        confidence=confidence,
    )
    return {
        "report": report.model_dump(),
        "trace": [
            {
                "node": "remediation_writer",
                "num_citations": len(result.citations),
                "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                "completion_tokens": response.usage.completion_tokens if response.usage else None,
            }
        ],
    }
