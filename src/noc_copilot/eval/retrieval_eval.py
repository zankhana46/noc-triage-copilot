"""Recall@k / MRR evaluation of runbook retrieval against incident bundles
with known-correct runbook ground truth (see Phase 2 acceptance in SPEC.md).
"""

from __future__ import annotations

from noc_copilot.retrieval.hybrid import HybridRetriever
from noc_copilot.simulator.schema import IncidentBundle

K_VALUES = (1, 3, 5)
MAX_TRUE_ALARMS_IN_QUERY = 8


def build_query(bundle: IncidentBundle) -> str:
    """The query an agent would plausibly search with: the human-readable
    text of the incident's real (non-noise) alarms, not the hidden ground
    truth itself and not the internal alarm_type code (a real NOC query
    would be built from what the alarm displays, not its taxonomy key)."""
    true_alarms = [a for a in bundle.alarms if a.incident_id is not None]
    texts = [a.message for a in true_alarms[:MAX_TRUE_ALARMS_IN_QUERY]]
    return " ".join(texts)


def evaluate_mode(
    retriever: HybridRetriever, bundles: list[IncidentBundle], mode: str
) -> dict:
    k_max = max(K_VALUES)
    hits_at_k = dict.fromkeys(K_VALUES, 0)
    reciprocal_ranks: list[float] = []
    n_scored = 0

    for bundle in bundles:
        query = build_query(bundle)
        if not query.strip():
            continue
        n_scored += 1
        results = retriever.retrieve(query, mode, top_k=k_max)
        ranked_runbook_ids = [r.runbook_id for r in results]
        rank = next(
            (i + 1 for i, rid in enumerate(ranked_runbook_ids) if rid == bundle.runbook_id),
            None,
        )
        reciprocal_ranks.append(1.0 / rank if rank else 0.0)
        for k in K_VALUES:
            if rank is not None and rank <= k:
                hits_at_k[k] += 1

    return {
        "mode": mode,
        "n_queries": n_scored,
        "mrr": round(sum(reciprocal_ranks) / n_scored, 4) if n_scored else 0.0,
        **{f"recall@{k}": round(hits_at_k[k] / n_scored, 4) if n_scored else 0.0 for k in K_VALUES},
    }


def load_bundles(path) -> list[IncidentBundle]:
    bundles = []
    with open(path) as f:
        for line in f:
            line = line.strip()
            if line:
                bundles.append(IncidentBundle.model_validate_json(line))
    return bundles
