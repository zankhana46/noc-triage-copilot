"""Alarm message rendering (vendor-specific templates) and background noise."""

from __future__ import annotations

import json
import random
import re
import uuid
from datetime import UTC, datetime, timedelta
from pathlib import Path

from openai.types.chat import ChatCompletionMessageParam

from noc_copilot.llm.client import chat
from noc_copilot.simulator.schema import Alarm, Node, Severity

VENDOR_CODES = {
    "Northeon": "NEO",
    "Vendorix": "VDX",
    "Arcadia Networks": "ARC",
}

# Vendor-flavored phrasing for the alarm types that show up most often as a
# root-cause signature. Anything not listed here falls back to GENERIC_TEMPLATES.
VENDOR_TEMPLATES: dict[tuple[str, str], list[str]] = {
    ("Northeon", "link_down"): ["LOS detected on {node}: optical signal lost"],
    ("Vendorix", "link_down"): ["LINK-DOWN-ALM: {node} interface down"],
    ("Arcadia Networks", "link_down"): ["L2_LINK_FAULT raised for {node}"],
    ("Northeon", "mains_failure"): ["AC-INPUT-FAIL: mains supply lost at {node}"],
    ("Vendorix", "mains_failure"): ["PWR-MAINS-ALM: commercial power failure, {node}"],
    ("Arcadia Networks", "mains_failure"): ["Mains fail at {node} — on battery"],
    ("Northeon", "sync_loss_gps_ptp"): ["SYNC-REF-LOSS: GPS/PTP reference lost at {node}"],
    ("Vendorix", "sync_loss_gps_ptp"): ["CLK-SYNC-ALM: timing source unavailable, {node}"],
    ("Arcadia Networks", "sync_loss_gps_ptp"): ["Frequency sync alarm: {node} free-running"],
    ("Northeon", "card_failure"): ["HW-CARD-FAIL: line card fault on {node}"],
    ("Vendorix", "card_failure"): ["SLOT-FAULT-ALM: hardware failure, {node}"],
    ("Arcadia Networks", "card_failure"): ["Board failure detected: {node}"],
    ("Northeon", "high_ber"): ["RF-DEGRADE: elevated BER on microwave hop {node}"],
    ("Vendorix", "high_ber"): ["MW-BER-ALM: bit error rate threshold exceeded, {node}"],
    ("Arcadia Networks", "high_ber"): ["High bit error rate on {node} radio link"],
    ("Northeon", "high_prb_utilization"): ["CELL-LOAD-HIGH: PRB utilization >90% on {node}"],
    ("Vendorix", "high_prb_utilization"): ["RRU-CONGEST-ALM: resource block exhaustion, {node}"],
    ("Arcadia Networks", "high_prb_utilization"): ["Congestion warning: {node} PRB usage critical"],
}

GENERIC_TEMPLATES: dict[str, list[str]] = {
    "link_down": ["Interface fault: {node} unreachable"],
    "backhaul_failure": ["Backhaul connectivity lost for {node}"],
    "s1_ng_failure": ["S1/NG interface failure reported by {node}"],
    "cell_outage": ["Cell service outage at {node}"],
    "mains_failure": ["Commercial power failure at {node}"],
    "battery_low": ["Battery reserve critically low at {node}"],
    "site_down": ["Site {node} declared down"],
    "card_failure": ["Hardware card failure on {node}"],
    "link_flap": ["Interface flapping detected on {node}"],
    "throughput_degradation": ["Throughput degradation observed at {node}"],
    "sync_loss_gps_ptp": ["Timing synchronization lost at {node}"],
    "handover_failure": ["Handover failure rate elevated at {node}"],
    "high_ber": ["Elevated bit error rate on {node}"],
    "intermittent_packet_loss": ["Intermittent packet loss detected on {node}"],
    "latency_alarm": ["Latency threshold exceeded on {node}"],
    "high_prb_utilization": ["PRB utilization threshold exceeded on {node}"],
    "rrc_setup_failure": ["RRC setup failure rate elevated at {node}"],
    "throughput_drop": ["Throughput drop detected at {node}"],
    "high_temperature": ["High temperature alarm at {node}"],
    "fan_failure": ["Cooling fan failure at {node}"],
    "equipment_shutdown": ["Equipment thermal shutdown at {node}"],
    "elevated_signalling_latency": ["Elevated signalling latency at {node}"],
    "attach_registration_failure": ["Attach/registration failure reported via {node}"],
    # background noise
    "config_changed": ["Configuration change applied on {node}"],
    "heartbeat_intermittent": ["Intermittent heartbeat loss from {node}"],
    "threshold_crossing_minor": ["Minor performance threshold crossing on {node}"],
    "ntp_drift_minor": ["Minor NTP clock drift on {node}"],
    "optical_power_fluctuation": ["Optical power fluctuation on {node}"],
}

SEVERITY_BY_ALARM_TYPE: dict[str, Severity] = {
    "link_down": Severity.CRITICAL,
    "backhaul_failure": Severity.CRITICAL,
    "s1_ng_failure": Severity.MAJOR,
    "cell_outage": Severity.CRITICAL,
    "mains_failure": Severity.MAJOR,
    "battery_low": Severity.CRITICAL,
    "site_down": Severity.CRITICAL,
    "card_failure": Severity.CRITICAL,
    "link_flap": Severity.MAJOR,
    "throughput_degradation": Severity.MINOR,
    "sync_loss_gps_ptp": Severity.MAJOR,
    "handover_failure": Severity.MAJOR,
    "high_ber": Severity.MINOR,
    "intermittent_packet_loss": Severity.MAJOR,
    "latency_alarm": Severity.MINOR,
    "high_prb_utilization": Severity.MINOR,
    "rrc_setup_failure": Severity.MAJOR,
    "throughput_drop": Severity.MAJOR,
    "high_temperature": Severity.WARNING,
    "fan_failure": Severity.MAJOR,
    "equipment_shutdown": Severity.CRITICAL,
    "elevated_signalling_latency": Severity.MAJOR,
    "attach_registration_failure": Severity.MAJOR,
    "config_changed": Severity.WARNING,
    "heartbeat_intermittent": Severity.WARNING,
    "threshold_crossing_minor": Severity.MINOR,
    "ntp_drift_minor": Severity.WARNING,
    "optical_power_fluctuation": Severity.MINOR,
}

NOISE_ALARM_TYPES = [
    "config_changed",
    "heartbeat_intermittent",
    "threshold_crossing_minor",
    "ntp_drift_minor",
    "optical_power_fluctuation",
]


# When set via enable_llm_paraphrasing(), render_message draws from this
# instead of the static templates above — real operator-jargon phrasing,
# generated independently of the runbook text (which comes from a
# different prompt and outline), so alarm evidence and retrieval targets
# don't share vocabulary just because one author wrote both.
_paraphrase_cache: dict[str, list[str]] | None = None
_PARAPHRASE_VARIANTS_PER_COMBO = 4
_CODE_FENCE_RE = re.compile(r"^```(?:json)?\s*|\s*```$", re.IGNORECASE | re.MULTILINE)


def _request_vendor_variants(vendor: str, alarm_types: list[str]) -> dict[str, list[str]]:
    """One LLM call per vendor, covering every alarm type at once (instead
    of one call per (vendor, alarm_type) pair) — free-tier daily request
    quotas are small enough that this ~27x reduction in call count matters."""
    alarm_list = "\n".join(
        f'[{at}] {at.replace("_", " ")} — example wording: "{GENERIC_TEMPLATES[at][0]}"'
        for at in alarm_types
    )
    messages: list[ChatCompletionMessageParam] = [
        {
            "role": "system",
            "content": (
                f"You generate realistic NOC alarm log message variants for a {vendor} "
                "network element, in that vendor's terse CLI/alarm-log style. For EACH "
                "alarm meaning below, write "
                f"{_PARAPHRASE_VARIANTS_PER_COMBO} short, distinct alarm message variants. "
                "Keep the literal placeholder '{node}' unchanged in every variant. Reply "
                "with ONLY a JSON object mapping each alarm's exact bracketed key "
                "to its list of message variants — no explanation, no markdown fences."
            ),
        },
        {"role": "user", "content": alarm_list},
    ]
    raw = chat(messages, temperature=0.9)
    parsed = json.loads(_CODE_FENCE_RE.sub("", raw).strip())
    if not isinstance(parsed, dict):
        raise ValueError("malformed paraphrase response: expected a JSON object")
    return parsed


def build_paraphrase_cache(cache_path: Path, vendors: list[str]) -> dict[str, list[str]]:
    """LLM-paraphrased alarm message variants per (vendor, alarm_type), cached
    to cache_path so repeated runs don't re-call the LLM and stay reproducible."""
    cache: dict[str, list[str]] = json.loads(cache_path.read_text()) if cache_path.exists() else {}
    alarm_types = list(SEVERITY_BY_ALARM_TYPE)
    updated = False
    for vendor in vendors:
        if all(f"{vendor}::{at}" in cache for at in alarm_types):
            continue
        try:
            parsed = _request_vendor_variants(vendor, alarm_types)
        except Exception:
            parsed = {}
        for alarm_type in alarm_types:
            key = f"{vendor}::{alarm_type}"
            if key in cache:
                continue
            variants = parsed.get(alarm_type)
            if not (
                isinstance(variants, list)
                and variants
                and all(isinstance(v, str) and "{node}" in v for v in variants)
            ):
                # fall back to the deterministic template
                variants = [GENERIC_TEMPLATES[alarm_type][0]]
            cache[key] = variants
            updated = True
    if updated:
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        cache_path.write_text(json.dumps(cache, indent=2))
    return cache


def enable_llm_paraphrasing(cache: dict[str, list[str]]) -> None:
    global _paraphrase_cache
    _paraphrase_cache = cache


def render_message(vendor: str, alarm_type: str, node_id: str, rng: random.Random) -> str:
    if _paraphrase_cache is not None:
        variants = _paraphrase_cache.get(f"{vendor}::{alarm_type}")
        if variants:
            return rng.choice(variants).format(node=node_id)
    templates = VENDOR_TEMPLATES.get((vendor, alarm_type)) or GENERIC_TEMPLATES[alarm_type]
    return rng.choice(templates).format(node=node_id)


def build_alarm(
    node: Node,
    alarm_type: str,
    timestamp: datetime,
    rng: random.Random,
    incident_id: str | None,
    severity: Severity | None = None,
) -> Alarm:
    return Alarm(
        alarm_id=str(uuid.uuid4()),
        timestamp=timestamp.isoformat(),
        node_id=node.id,
        alarm_type=alarm_type,
        severity=severity or SEVERITY_BY_ALARM_TYPE[alarm_type],
        vendor_code=VENDOR_CODES[node.vendor],
        message=render_message(node.vendor, alarm_type, node.id, rng),
        incident_id=incident_id,
    )


def generate_background_noise(
    nodes: list[Node],
    window_start: datetime,
    window_end: datetime,
    count: int,
    rng: random.Random,
) -> list[Alarm]:
    """Unrelated alarms (incident_id=None) scattered through the window."""
    span = (window_end - window_start).total_seconds()
    noise = []
    for _ in range(count):
        node = rng.choice(nodes)
        alarm_type = rng.choice(NOISE_ALARM_TYPES)
        ts = window_start + timedelta(seconds=rng.uniform(0, span))
        noise.append(build_alarm(node, alarm_type, ts, rng, incident_id=None))
    return noise


def apply_flapping_and_duplicates(
    alarms: list[Alarm],
    flap_probability: float,
    flap_repeats: tuple[int, int],
    duplicate_probability: float,
    rng: random.Random,
) -> list[Alarm]:
    """Duplicate some alarms (exact repeats) and flap some (repeats a few
    seconds apart), simulating noisy real-world alarm feeds."""
    out: list[Alarm] = []
    for alarm in alarms:
        out.append(alarm)
        if rng.random() < duplicate_probability:
            dup = alarm.model_copy(update={"alarm_id": str(uuid.uuid4())})
            out.append(dup)
        if rng.random() < flap_probability:
            base_ts = datetime.fromisoformat(alarm.timestamp)
            repeats = rng.randint(*flap_repeats)
            for r in range(repeats):
                flap_ts = base_ts + timedelta(seconds=rng.uniform(2, 20) * (r + 1))
                out.append(
                    alarm.model_copy(
                        update={
                            "alarm_id": str(uuid.uuid4()),
                            "timestamp": flap_ts.isoformat(),
                        }
                    )
                )
    return out


def utcnow() -> datetime:
    return datetime.now(UTC)
