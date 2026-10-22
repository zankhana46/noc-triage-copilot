"""Runbook Retriever node: retrieves runbook sections for the top
hypothesis using the Phase 2 hybrid retriever (mode is configurable for
ablation — see configs/retrieval.yaml / state["retrieval_mode"]).
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from noc_copilot.agents.state import TriageState
from noc_copilot.retrieval.hybrid import HybridRetriever

DEFAULT_RETRIEVAL_MODE = "hybrid+rerank"
MAX_ALARMS_IN_FALLBACK_QUERY = 5


def _build_query(state: TriageState) -> str:
    hypotheses = state.get("hypotheses", [])
    if hypotheses:
        top = hypotheses[0]
        meaning = top["root_cause_type"].replace("_", " ")
        return f"{meaning}: {top['rationale']}"
    # no diagnosis at all (e.g. graph-tools-off ablation with a weak signal):
    # fall back to raw alarm evidence
    alarms = state.get("correlated_alarms", [])
    return " ".join(a["message"] for a in alarms[:MAX_ALARMS_IN_FALLBACK_QUERY])


def make_runbook_retriever_node(
    retriever: HybridRetriever, top_k: int
) -> Callable[[TriageState], dict[str, Any]]:
    def runbook_retriever(state: TriageState) -> dict[str, Any]:
        mode = state.get("retrieval_mode", DEFAULT_RETRIEVAL_MODE)
        query = _build_query(state)
        chunks = retriever.retrieve(query, mode, top_k=top_k) if query.strip() else []
        return {
            "retrieved_chunks": [c.model_dump() for c in chunks],
            "trace": [
                {
                    "node": "runbook_retriever",
                    "retrieval_mode": mode,
                    "num_chunks": len(chunks),
                    "query": query,
                }
            ],
        }

    return runbook_retriever
