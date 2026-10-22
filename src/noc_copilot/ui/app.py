"""Streamlit UI: select or paste an incident, run it through the FastAPI
triage backend, and view the agent trace, topology path, and report
(SPEC.md 6.2).

Run with: uv run --extra dev streamlit run src/noc_copilot/ui/app.py
"""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import requests
import streamlit as st

REPO_ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(REPO_ROOT / "src"))

from noc_copilot.eval.retrieval_eval import load_bundles  # noqa: E402

API_BASE_URL = os.environ.get("TRIAGE_API_URL", "http://127.0.0.1:8000")
RETRIEVAL_MODES = ["bm25", "dense", "hybrid", "hybrid+rerank"]

st.set_page_config(page_title="NOC Triage Copilot", layout="wide")
st.title("NOC Triage Copilot")


@st.cache_data
def _load_split(split: str) -> list[dict]:
    path = REPO_ROOT / "data" / "incidents" / f"{split}.jsonl"
    if not path.exists():
        return []
    return [b.model_dump() for b in load_bundles(path)]


with st.sidebar:
    st.header("Incident source")
    source = st.radio("Choose an incident", ["Select from test data", "Paste alarm JSON"])

    bundle: dict | None = None
    alarms: list[dict] = []

    if source == "Select from test data":
        split = st.selectbox("Split", ["test", "dev", "train"])
        bundles = _load_split(split)
        if not bundles:
            st.warning(
                f"No data at data/incidents/{split}.jsonl — run scripts/01_generate_data.py first"
            )
        else:
            failure_types = sorted({b["failure_type"] for b in bundles})
            chosen_type = st.selectbox("Failure type", ["(any)", *failure_types])
            pool = [
                b for b in bundles if chosen_type == "(any)" or b["failure_type"] == chosen_type
            ]
            labels = [
                f"{b['incident_id'][:8]} — {b['failure_type']} @ {b['root_cause_node']}"
                for b in pool
            ]
            idx = (
                st.selectbox("Incident", range(len(pool)), format_func=lambda i: labels[i])
                if pool
                else None
            )
            if idx is not None:
                bundle = pool[idx]
                alarms = [
                    {k: v for k, v in a.items() if k != "incident_id"} for a in bundle["alarms"]
                ]
    else:
        pasted = st.text_area("Alarm window JSON (list of alarm objects)", height=200)
        if pasted.strip():
            try:
                alarms = json.loads(pasted)
            except json.JSONDecodeError as e:
                st.error(f"Invalid JSON: {e}")

    st.header("Ablation controls")
    use_graph_tools = st.checkbox("Use graph tools", value=True)
    retrieval_mode = st.selectbox(
        "Retrieval mode", RETRIEVAL_MODES, index=RETRIEVAL_MODES.index("hybrid+rerank")
    )
    run_clicked = st.button("Run triage", type="primary", disabled=not alarms)

if bundle is not None:
    with st.expander("Ground truth (hidden from the copilot)", expanded=False):
        st.json(
            {
                "root_cause_node": bundle["root_cause_node"],
                "root_cause_type": bundle["root_cause_type"],
                "affected_nodes": bundle["affected_nodes"],
                "runbook_id": bundle["runbook_id"],
            }
        )

if alarms:
    with st.expander(f"Alarm window ({len(alarms)} alarms)", expanded=False):
        st.dataframe(alarms, use_container_width=True)

if run_clicked:
    with st.spinner("Running triage pipeline..."):
        try:
            response = requests.post(
                f"{API_BASE_URL}/triage",
                json={
                    "alarms": alarms,
                    "use_graph_tools": use_graph_tools,
                    "retrieval_mode": retrieval_mode,
                },
                timeout=120,
            )
            response.raise_for_status()
            result = response.json()
        except requests.RequestException as e:
            st.error(f"Could not reach the triage API at {API_BASE_URL}: {e}")
            st.stop()

    report = result.get("report") or {}
    col1, col2 = st.columns([2, 1])

    with col1:
        st.subheader("Incident report")
        st.markdown(f"**Summary:** {report.get('incident_summary', '(none)')}")
        st.markdown(
            f"**Root cause:** `{report.get('root_cause_node')}` "
            f"({report.get('root_cause_type')}, confidence {report.get('confidence', 0):.2f})"
        )
        if bundle is not None:
            match = report.get("root_cause_node") == bundle["root_cause_node"]
            st.markdown(f"**Node match vs. ground truth:** {'✅' if match else '❌'}")

        st.markdown("**Top hypotheses:**")
        for h in report.get("top3_hypotheses", []):
            label = f"`{h['root_cause_node']}` ({h['root_cause_type']}, conf {h['confidence']:.2f})"
            st.markdown(f"- {label}: {h['rationale']}")

        st.markdown("**Remediation steps:**")
        for step in report.get("remediation_steps", []):
            st.markdown(f"- {step}")

        st.markdown("**Citations:**")
        for c in report.get("citations", []):
            st.markdown(f"- `{c['source']}`: {c['detail']}")

    with col2:
        st.subheader("Telemetry")
        st.metric("Latency (s)", f"{result['latency_seconds']:.2f}")
        st.metric("Total tokens", result["total_tokens"])
        st.metric("Tool calls", result["tool_calls"])
        st.metric("Diagnostic loops", result["num_diagnostic_loops"])

        st.subheader("Topology path (anchor → core)")
        evidence_path = result.get("evidence_path", [])
        if evidence_path:
            st.graphviz_chart(
                "digraph { rankdir=LR; " + " -> ".join(f'"{n["id"]}"' for n in evidence_path) + " }"
            )
        else:
            st.caption("(no topology evidence — graph tools were off, or no candidates found)")

    st.subheader("Agent trace")
    st.json(result.get("trace", []))
