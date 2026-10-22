"""Runbook generation: an LLM drafts Markdown runbooks from a fixed
structured outline per failure type, plus a few vendor-specific variants.
Outputs are written to disk and never regenerated once present, so the
corpus referenced by incident ground truth stays fixed across runs.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from openai.types.chat import ChatCompletionMessageParam

from noc_copilot.llm.client import chat
from noc_copilot.simulator.schema import FailureType

SECTIONS = ["Symptoms", "Probable causes", "Diagnostic steps", "Remediation", "Escalation"]

RUNBOOK_OUTLINES: dict[FailureType, dict[str, list[str]]] = {
    FailureType.FIBER_CUT: {
        "Symptoms": [
            "Link-down alarm on an aggregation or pre-aggregation fiber interface",
            "Downstream sites report S1/NG interface failures and cell outages",
        ],
        "Probable causes": [
            "Physical fiber cut (construction, rodent damage)",
            "Failed optical transceiver (SFP/SFP+)",
            "Patch panel or ODF fault",
        ],
        "Diagnostic steps": [
            "Check optical power levels (Tx/Rx) on both ends of the span",
            "Confirm whether the affected node has a redundant uplink; if so, verify failover",
            "Correlate with any scheduled fiber maintenance or third-party dig notifications",
            "Use OTDR trace if available to localize the fault distance",
        ],
        "Remediation": [
            "If a redundant path exists, confirm traffic has failed over and monitor",
            "Dispatch field technician to splice or replace the fiber span",
            "Swap suspect optical transceiver and re-verify link",
        ],
        "Escalation": [
            "No redundant path and outage exceeds 30 minutes: escalate to Tier 2 transport team",
            "Multiple sites affected: open a major incident bridge",
        ],
    },
    FailureType.SITE_POWER_FAILURE: {
        "Symptoms": [
            "Mains failure alarm, followed by battery-low alarm",
            "Site-down alarm and cell outages after battery depletion",
        ],
        "Probable causes": ["Commercial power outage", "Rectifier failure", "Blown site breaker"],
        "Diagnostic steps": [
            "Check utility outage maps for the site's service area",
            "Verify rectifier module status and output voltage remotely",
            "Estimate remaining battery runtime from telemetry",
        ],
        "Remediation": [
            "Dispatch generator or portable power if runtime is insufficient",
            "Replace failed rectifier module",
            "Restore mains and confirm batteries begin recharging",
        ],
        "Escalation": [
            "Battery runtime under 30 minutes with no ETA on power: escalate immediately",
            "Repeated power failures at the same site: open a facilities ticket",
        ],
    },
    FailureType.ROUTER_HARDWARE_FAULT: {
        "Symptoms": [
            "Card/hardware failure alarm on a router",
            "Interface flapping on directly attached links",
            "Degraded throughput on downstream nodes",
        ],
        "Probable causes": [
            "Failed line card or control module",
            "Firmware crash/reboot loop",
            "Power supply fault within the chassis",
        ],
        "Diagnostic steps": [
            "Check chassis health and card status via CLI or NMS",
            "Review crash logs / core dumps for the affected module",
            "Determine if a redundant card or route exists",
        ],
        "Remediation": [
            "Reset or reseat the affected card if safe to do so",
            "Fail over to redundant hardware if available",
            "Replace the faulty card via field dispatch",
        ],
        "Escalation": [
            "No redundant hardware and multiple downstream sites degraded: escalate to Tier 2",
            "Repeated hardware faults on the same chassis: open an RMA with vendor support",
        ],
    },
    FailureType.SYNC_LOSS: {
        "Symptoms": [
            "GPS/PTP sync-loss alarm at a site",
            "Handover failures and throughput degradation on that site's cells",
        ],
        "Probable causes": [
            "GPS antenna/cable fault or obstruction",
            "PTP grandmaster unreachable",
            "Faulty sync card",
        ],
        "Diagnostic steps": [
            "Check GPS antenna cable and connector for damage",
            "Verify PTP grandmaster reachability and path delay",
            "Check how long the node has been free-running and its holdover accuracy",
        ],
        "Remediation": [
            "Repair or replace GPS antenna/cable",
            "Re-point to an alternate PTP grandmaster if available",
            "Replace faulty sync card",
        ],
        "Escalation": [
            "Holdover accuracy degrading beyond spec: escalate before handover failures spread",
            "Multiple sites losing sync from the same grandmaster: escalate to core sync team",
        ],
    },
    FailureType.MICROWAVE_DEGRADATION: {
        "Symptoms": [
            "Elevated bit-error-rate alarm on a microwave hop",
            "Intermittent packet loss and latency alarms on downstream nodes",
        ],
        "Probable causes": [
            "Heavy rain/fog attenuation",
            "Antenna misalignment",
            "Interference from a new co-channel source",
        ],
        "Diagnostic steps": [
            "Check current and forecast weather at both hop ends",
            "Review received signal level (RSL) trend for drift vs. a sudden step change",
            "Check for newly registered interference sources in the band",
        ],
        "Remediation": [
            "If weather-related, monitor; alarm typically clears as conditions improve",
            "Re-align antenna if RSL shows a sudden step change",
            "Coordinate frequency change if interference is confirmed",
        ],
        "Escalation": [
            "BER remains high after weather clears: escalate to RF engineering",
            "Service-affecting outage, not just degradation: escalate immediately",
        ],
    },
    FailureType.CELL_CONGESTION: {
        "Symptoms": [
            "High PRB utilization alarm on a cell",
            "RRC setup failures and throughput drop on the same cell",
        ],
        "Probable causes": [
            "Organic traffic growth",
            "Temporary event-driven demand spike",
            "Neighboring cell outage shifting load",
        ],
        "Diagnostic steps": [
            "Check PRB utilization trend over the past 24-72 hours",
            "Check whether a neighboring cell is down and redirecting traffic",
            "Review subscriber count and busy-hour traffic mix",
        ],
        "Remediation": [
            "Enable additional carrier/sector capacity if available",
            "Apply traffic steering to neighboring cells with spare capacity",
            "Queue a capacity upgrade request if growth is sustained",
        ],
        "Escalation": [
            "Sustained congestion beyond KPI threshold: escalate to RF/capacity planning",
            "Event-driven spike exceeding temp. capacity: escalate to planning for COW/COLT",
        ],
    },
    FailureType.COOLING_FAILURE: {
        "Symptoms": [
            "High-temperature alarm at a site or cabinet",
            "Fan failure alarm, and possible equipment thermal shutdown",
        ],
        "Probable causes": [
            "Failed cabinet/site cooling fan",
            "Blocked air filter or vents",
            "HVAC unit failure",
        ],
        "Diagnostic steps": [
            "Check current cabinet/ambient temperature against threshold",
            "Verify fan RPM/status telemetry if available",
            "Check HVAC unit status for the site",
        ],
        "Remediation": [
            "Clear blocked vents/filters",
            "Replace failed fan module",
            "Dispatch portable cooling if HVAC repair will take time",
        ],
        "Escalation": [
            "Temperature approaching equipment shutdown threshold: escalate immediately",
            "Equipment has already shut down: open a major incident",
        ],
    },
    FailureType.CORE_OVERLOAD: {
        "Symptoms": [
            "Elevated signalling latency at a core element (AMF/UPF/core router)",
            "Attach/registration failures across many sites served by that element",
        ],
        "Probable causes": [
            "Traffic surge beyond provisioned capacity",
            "Software/control-plane resource exhaustion",
            "Upstream dependency (e.g. HSS/UDM) slowness",
        ],
        "Diagnostic steps": [
            "Check CPU/memory/session-table utilization on the core element",
            "Check for a correlated spike in attach/registration attempts",
            "Check health of upstream dependencies the element relies on",
        ],
        "Remediation": [
            "Scale out or fail over to a standby core element if available",
            "Throttle or rate-limit non-critical signalling if sustained",
            "Restart the affected process/VM only as a last resort, coordinated with change mgmt",
        ],
        "Escalation": [
            "Many sites affected and no failover available: open a major incident immediately",
            "Suspected upstream dependency failure: escalate to the owning team in parallel",
        ],
    },
}

VENDOR_VARIANT_PLAN: list[tuple[FailureType, str]] = [
    (FailureType.FIBER_CUT, "Northeon"),
    (FailureType.SYNC_LOSS, "Vendorix"),
    (FailureType.ROUTER_HARDWARE_FAULT, "Arcadia Networks"),
]


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-")


def runbook_id_for(failure_type: FailureType, vendor: str | None = None) -> str:
    if vendor is None:
        return failure_type.value
    return f"{failure_type.value}__{_slug(vendor)}"


def _build_prompt(
    failure_type: FailureType, outline: dict[str, list[str]], vendor: str | None
) -> list[ChatCompletionMessageParam]:
    vendor_clause = (
        f" Write it as a {vendor}-specific variant: use {vendor}'s CLI/terminology conventions "
        f"and mention {vendor} equipment explicitly where relevant."
        if vendor
        else ""
    )
    outline_text = "\n".join(
        f"## {section}\n" + "\n".join(f"- {bullet}" for bullet in bullets)
        for section, bullets in outline.items()
    )
    system = (
        "You are a senior telecom NOC engineer writing an internal runbook. "
        "Write clear, actionable Markdown using exactly these five H2 sections, "
        "in this order: Symptoms, Probable causes, Diagnostic steps, Remediation, Escalation. "
        "Expand each outline bullet into 1-2 concise sentences or a short sub-list. "
        "Do not add extra top-level sections. Do not include a title line."
    )
    user = (
        f"Failure type: {failure_type.value.replace('_', ' ')}.{vendor_clause}\n\n"
        f"Outline to expand:\n{outline_text}"
    )
    return [{"role": "system", "content": system}, {"role": "user", "content": user}]


def generate_all_runbooks(out_dir: Path) -> dict[str, dict]:
    """Writes one .md per runbook_id to out_dir (skipping ones that already
    exist, so the corpus is fixed once generated) and returns the index
    {runbook_id: {failure_type, vendor, path}}."""
    out_dir.mkdir(parents=True, exist_ok=True)
    index_path = out_dir / "runbooks_index.json"
    index: dict[str, dict] = json.loads(index_path.read_text()) if index_path.exists() else {}

    jobs: list[tuple[FailureType, str | None]] = [(ft, None) for ft in RUNBOOK_OUTLINES]
    jobs += [(ft, vendor) for ft, vendor in VENDOR_VARIANT_PLAN]

    for failure_type, vendor in jobs:
        rb_id = runbook_id_for(failure_type, vendor)
        md_path = out_dir / f"{rb_id}.md"
        if md_path.exists():
            index[rb_id] = {
                "failure_type": failure_type.value,
                "vendor": vendor,
                "path": md_path.name,
            }
            continue
        messages = _build_prompt(failure_type, RUNBOOK_OUTLINES[failure_type], vendor)
        body = chat(messages, temperature=0.3)
        title = f"# {failure_type.value.replace('_', ' ').title()} Runbook" + (
            f" ({vendor})" if vendor else ""
        )
        md_path.write_text(f"{title}\n\n{body.strip()}\n")
        index[rb_id] = {"failure_type": failure_type.value, "vendor": vendor, "path": md_path.name}

    index_path.write_text(json.dumps(index, indent=2))
    return index
