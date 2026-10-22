"""Diagnostician node: decides the root-cause TYPE and NODE from alarm
patterns plus topology evidence, outputting top-3 ranked hypotheses with
confidence via strict structured LLM output.
"""

from __future__ import annotations

from typing import Any

from openai.types.chat import ChatCompletionMessageParam

from noc_copilot.agents.state import DiagnosticianOutput, RootCauseCandidate, TriageState
from noc_copilot.llm.client import chat_structured

MAX_ALARMS_IN_PROMPT = 20

SYSTEM_PROMPT = """\
You are the Diagnostician in a NOC triage pipeline for a telecom mobile network. \
Given alarm evidence and (if available) topology evidence, determine the most likely \
root-cause TYPE and NODE.

The 8 known failure types are: fiber_cut, site_power_failure, router_hardware_fault, \
sync_loss, microwave_degradation, cell_congestion, cooling_failure, core_overload. \
Use "unknown" only if truly indeterminate.

root_cause_node must be one of the node ids mentioned in the evidence below, or null if \
none fits. Give your top 1-3 hypotheses ranked by confidence (most likely first). Set \
needs_more_evidence=true only if your top hypothesis's confidence is below 0.6 and \
additional topology context would plausibly help.\
"""


def format_alarms(alarms: list[dict]) -> str:
    lines = [
        f"- [{a['timestamp']}] {a['node_id']} ({a['severity']}, {a['alarm_type']}): {a['message']}"
        for a in alarms[:MAX_ALARMS_IN_PROMPT]
    ]
    return "\n".join(lines) if lines else "(no alarm evidence)"


def format_candidates(candidates: list[RootCauseCandidate], evidence_path: list[dict]) -> str:
    if not candidates:
        return "No topology evidence available — diagnose from alarm content alone."
    lines = [
        f"- {c['node_id']} (type={c['node_type']}, vendor={c['vendor']}, "
        f"direct_alarm={c['has_direct_alarm']}, redundant_uplink={c['has_redundant_uplink']}, "
        f"depth_from_core={c['depth_from_core']}): {c['rank_reason']}"
        for c in candidates
    ]
    path_str = " -> ".join(n["id"] for n in evidence_path) if evidence_path else "(none)"
    return (
        "Topology evidence (ranked root-cause node candidates):\n"
        + "\n".join(lines)
        + f"\n\nUpstream path from the likely anchor point to the core: {path_str}"
    )


def diagnostician(state: TriageState) -> dict[str, Any]:
    alarms = state.get("correlated_alarms", [])
    candidates = state.get("root_cause_candidates", [])
    evidence_path = state.get("evidence_path", [])

    candidates_section = format_candidates(candidates, evidence_path)
    user_content = f"Alarm evidence:\n{format_alarms(alarms)}\n\n{candidates_section}"
    messages: list[ChatCompletionMessageParam] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": user_content},
    ]
    result, response = chat_structured(messages, DiagnosticianOutput, temperature=0.2)
    top_confidence = result.hypotheses[0].confidence if result.hypotheses else 0.0

    return {
        "hypotheses": [h.model_dump() for h in result.hypotheses],
        "confidence": top_confidence,
        "trace": [
            {
                "node": "diagnostician",
                "loop_count": state.get("loop_count", 0),
                "needs_more_evidence": result.needs_more_evidence,
                "top_confidence": top_confidence,
                "prompt_tokens": response.usage.prompt_tokens if response.usage else None,
                "completion_tokens": response.usage.completion_tokens if response.usage else None,
            }
        ],
    }


CONFIDENCE_THRESHOLD = 0.6
DEFAULT_MAX_DIAGNOSTIC_LOOPS = 2


def route_after_diagnostician(state: TriageState) -> str:
    confidence = state.get("confidence", 1.0)
    loop_count = state.get("loop_count", 0)
    max_loops = state.get("max_diagnostic_loops", DEFAULT_MAX_DIAGNOSTIC_LOOPS)
    if confidence < CONFIDENCE_THRESHOLD and loop_count < max_loops:
        return "topology_analyst"
    return "runbook_retriever"
