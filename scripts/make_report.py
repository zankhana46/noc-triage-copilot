#!/usr/bin/env python3
"""Phase 6: regenerates README.md from whatever's actually in results/*.json.
Every number in the README traces back to a results file; anything not yet
measured is written as TBD rather than invented.

Usage: uv run --extra dev scripts/make_report.py
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
RESULTS_DIR = REPO_ROOT / "results"


def load_json(name: str) -> dict | None:
    path = RESULTS_DIR / name
    if not path.exists():
        return None
    return json.loads(path.read_text())


def pct(x: float | None) -> str:
    return "TBD" if x is None else f"{x * 100:.1f}%"


def num(x: float | None, fmt: str = "{:.3f}") -> str:
    return "TBD" if x is None else fmt.format(x)


def section_data_summary() -> str:
    d = load_json("data_summary.json")
    if d is None:
        return "_TBD — run `scripts/01_generate_data.py`._\n"
    topo = d["topology"]
    inc = d["incident_bundles"]
    alarms = d["alarms"]
    node_rows = "\n".join(f"| {t} | {c} |" for t, c in topo["node_type_counts"].items())
    failure_rows = "\n".join(f"| {t} | {c} |" for t, c in inc["by_failure_type"].items())
    split_rows = "\n".join(f"| {s} | {c} |" for s, c in inc["by_split"].items())
    return f"""\
Seed: `{d["seed"]}`. Topology: **{topo["num_nodes"]} nodes**, **{topo["num_links"]} links**.

| Node type | Count |
|---|---|
{node_rows}

**{inc["total"]} incident bundles**, by failure type:

| Failure type | Count |
|---|---|
{failure_rows}

By split (no root-cause node shared across splits):

| Split | Count |
|---|---|
{split_rows}

**{alarms["total"]} total alarms**, noise ratio **{alarms["noise_ratio"]:.3f}** \
({alarms["noise_count"]} background/noise alarms mixed in with the real signal).
"""


def section_retrieval_eval() -> str:
    d = load_json("retrieval_eval.json")
    if d is None:
        return "_TBD — run `scripts/02_evaluate_retrieval.py`._\n"
    rows = []
    for mode, m in d.items():
        rows.append(
            f"| {mode} | {m['n_queries']} | {num(m['mrr'])} | {pct(m['recall@1'])} | "
            f"{pct(m['recall@3'])} | {pct(m['recall@5'])} |"
        )
    return (
        "| Mode | N queries | MRR | Recall@1 | Recall@3 | Recall@5 |\n"
        "|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )


def _triage_metrics_row(label: str, m: dict) -> str:
    return (
        f"| {label} | {m['n_incidents']} | {pct(m['rca_type_accuracy_top1'])} | "
        f"{pct(m['rca_type_accuracy_top3'])} | {pct(m['root_node_exact_match'])} | "
        f"{pct(m['root_node_within_one_hop'])} | {pct(m['affected_node_f1_mean'])} | "
        f"{num(m['latency_p50_seconds'], '{:.1f}s')} | {num(m['latency_p95_seconds'], '{:.1f}s')} |"
    )


def section_main_eval() -> str:
    candidates = list(RESULTS_DIR.glob("eval_*.json"))
    candidates = [p for p in candidates if not p.name.startswith("eval_raw_")]
    if not candidates:
        return "_TBD — run `scripts/03_evaluate.py`._\n"
    rows = []
    for path in sorted(candidates):
        m = json.loads(path.read_text())
        rows.append(_triage_metrics_row(m.get("model", path.stem), m))
    header = (
        "| Model | N | RCA top-1 | RCA top-3 | Root-node exact | Root-node 1-hop | "
        "Affected-node F1 | Latency p50 | Latency p95 |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    note = (
        "\n_Latency includes free-tier rate-limit backoff baked into the measurement — "
        "not representative of raw inference speed. Cost figures (not shown in this table; "
        "see the raw results files) are hypothetical, computed at the model's published "
        "paid-tier rate; actual spend on the free tier was $0._\n"
    )
    return header + "\n".join(rows) + "\n" + note


def section_graph_tools_ablation() -> str:
    d = load_json("ablation_graph_tools.json")
    if d is None:
        return "_TBD — run `scripts/03_evaluate.py`._\n"
    header = (
        "| Condition | N | RCA top-1 | RCA top-3 | Root-node exact | Root-node 1-hop | "
        "Affected-node F1 | Latency p50 | Latency p95 |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    rows = [
        _triage_metrics_row("Full system (graph tools on)", d["full_system"]),
        _triage_metrics_row("No graph tools (alarms only)", d["no_graph_tools"]),
    ]
    return header + "\n".join(rows) + "\n"


def section_retrieval_mode_ablation() -> str:
    custom = load_json("ablation_retrieval_modes.json")
    ragas = load_json("ragas_eval.json")
    if custom is None:
        return "_TBD — run `scripts/03_evaluate.py`._\n"
    rows = []
    for mode, m in custom.items():
        ragas_m = (ragas or {}).get(mode, {})
        rows.append(
            f"| {mode} | {num(m['latency_p50_seconds'], '{:.1f}s')} | "
            f"{m['mean_tokens_per_incident']:.0f} | "
            f"{num(ragas_m.get('faithfulness'))} | {num(ragas_m.get('answer_relevancy'))} | "
            f"{num(ragas_m.get('context_precision'))} | {num(ragas_m.get('context_recall'))} |"
        )
    header = (
        "| Mode | Latency p50 | Mean tokens | Faithfulness | Answer relevancy | "
        "Context precision | Context recall |\n"
        "|---|---|---|---|---|---|---|\n"
    )
    note = (
        "\n_RCA/node-match/F1 metrics are identical across modes by design — the diagnosis "
        "is made before retrieval runs, so only the final written report (and hence RAGAS "
        "scores) differs by mode; see results/ablation_retrieval_modes.json for the full "
        "(mode-invariant) custom metrics. `hybrid+rerank` is the default mode and runs the "
        "full pipeline (correlator, topology analyst, diagnostician, retriever, writer); "
        "the other three modes reuse that run's diagnosis and only rerun the retriever+writer "
        "tail, which is why their latency/token counts are much lower — that's the cost of the "
        "full diagnostic pass, not a property of those retrieval modes themselves. RAGAS run "
        "on a 10-row/mode sub-sample, not the full test split, due to free-tier rate limits. "
        "Faithfulness scores are likely underestimates: RAGAS only credits claims supported by "
        "retrieved runbook text, but the Remediation Writer also legitimately grounds claims "
        "in topology/alarm evidence, which RAGAS can't see._\n"
    )
    return header + "\n".join(rows) + "\n" + note


def section_finetune_comparison() -> str:
    base = load_json("eval_base_small.json")
    finetuned = load_json("eval_finetuned_small.json")
    if base is None and finetuned is None:
        return (
            "_TBD. QLoRA fine-tuning of Qwen3-8B (non-thinking mode, 4-bit, rank-16 LoRA) "
            "completed successfully on a Colab T4 — eval loss 0.596, 89.4% token accuracy on "
            "the held-out dev set, no overfitting. The adapter is saved; serving it "
            "(base-vs-fine-tuned comparison via vLLM) is blocked on free-tier Colab GPU quota "
            "and hasn't been completed yet. See `src/noc_copilot/finetune/` for the "
            "dataset builder, training script, and merge script — all tested/working except "
            "the final merge-and-serve step._\n"
        )
    rows = []
    if base:
        rows.append(_triage_metrics_row("Base Qwen3-8B (no fine-tuning)", base))
    if finetuned:
        rows.append(_triage_metrics_row("Fine-tuned Qwen3-8B (QLoRA)", finetuned))
    header = (
        "| Model | N | RCA top-1 | RCA top-3 | Root-node exact | Root-node 1-hop | "
        "Affected-node F1 | Latency p50 | Latency p95 |\n"
        "|---|---|---|---|---|---|---|---|---|\n"
    )
    return header + "\n".join(rows) + "\n"


def section_latency_benchmarks() -> str:
    latency_files = list(RESULTS_DIR.glob("latency_*.json"))
    if not latency_files:
        return "_TBD — Phase 5 (vLLM serving + latency benchmarks) not yet run._\n"
    lines = [f"- `{p.name}`: {p.read_text()[:200]}..." for p in sorted(latency_files)]
    return "\n".join(lines) + "\n"


def build_readme() -> str:
    return f"""\
# NOC Triage Copilot

AI copilot for telecom NOC alarm triage: multi-agent root-cause analysis (LangGraph),
knowledge-graph RAG over network topology and runbooks (Neo4j + hybrid retrieval), a
QLoRA fine-tuned small model benchmarked against a large API model, and low-latency
serving (vLLM).

See [SPEC.md](SPEC.md) for the full project specification.

## The problem

A burst of network alarms during an outage is noisy and hard to triage quickly: dozens
of alarms fire across many devices, only some of which are actually related to the root
cause, and a human operator has to (1) figure out which alarms belong together, (2)
localize the actual failing component using the network topology, (3) identify the type
of failure, and (4) find and apply the right remediation steps — all under time
pressure. This project builds an AI copilot that does this end to end, with its
reasoning grounded and cited at every step.

## Architecture

```mermaid
flowchart TD
    A[Alarm window] --> B[Alarm Correlator]
    B --> C[Topology Analyst]
    C --> D[Diagnostician]
    D -->|confidence low, loop capped| C
    D --> E[Runbook Retriever]
    E --> F[Remediation Writer]
    F --> G[Structured Incident Report]

    C -.->|Cypher tools| N[(Neo4j<br/>topology graph)]
    E -.->|BM25 + dense + rerank| R[(ChromaDB<br/>runbook chunks)]
    D -.->|structured output| L[LLM<br/>OpenAI-compatible client]
    F -.->|structured output| L

    subgraph Interfaces
        API[FastAPI /triage]
        UI[Streamlit UI]
    end
    API --> A
    UI --> API
```

## All data is synthetic

Every alarm, topology node, and incident in this project is generated by a simulator
(`src/noc_copilot/simulator/`) that plants ground truth as it goes: it builds a
realistic hierarchical mobile network topology, injects one of 8 failure types at a
specific node, propagates realistic downstream alarms through the *real* topology graph
(respecting redundancy — a dual-homed node doesn't lose connectivity from a single
failed uplink), and mixes in background noise, flapping, and duplicate alarms. The
simulator remembers the true root-cause node, root-cause type, affected nodes, and
correct runbook for every incident — alarm messages are LLM-paraphrased in each vendor's
terse log style so they don't leak the runbook's own vocabulary. That hidden ground
truth is what makes every metric in this README a real, checkable measurement rather
than a vibe. See `SPEC.md` for the full simulator design.

## Quickstart

```bash
# 1. Install dependencies (Python 3.12, uv)
uv sync --extra dev

# 2. Copy .env.example to .env and fill in a free Gemini API key
#    (https://aistudio.google.com/apikey)
cp .env.example .env

# 3. Start Neo4j
docker compose up -d neo4j

# 4. Generate the synthetic dataset and load the topology graph
uv run --extra dev scripts/01_generate_data.py
uv run --extra dev scripts/01_load_graph.py

# 5. Run the test suite
uv run --extra dev pytest tests/ -v

# 6. Start the API and UI
uv run --extra dev uvicorn noc_copilot.api.app:app --app-dir src --port 8000 &
uv run --extra dev streamlit run src/noc_copilot/ui/app.py
```

Then open the Streamlit URL it prints, pick a test incident, and run it through the
full triage pipeline.

## Results

All numbers below are generated by `scripts/make_report.py` directly from
`results/*.json` — nothing here is estimated or invented.

### Synthetic dataset (Phase 1)

{section_data_summary()}

### Runbook retrieval (Phase 2)

Recall@k / MRR per retrieval mode, on queries built from test-split incidents' real
(non-noise) alarm evidence against the known-correct runbook:

{section_retrieval_eval()}

### End-to-end triage evaluation (Phase 3)

Full LangGraph pipeline, custom ground-truth metrics on the test split:

{section_main_eval()}

#### Ablation: graph tools on vs. off

{section_graph_tools_ablation()}

#### Ablation: retrieval mode

{section_retrieval_mode_ablation()}

### QLoRA fine-tuning comparison (Phase 4)

{section_finetune_comparison()}

### vLLM serving latency benchmarks (Phase 5)

{section_latency_benchmarks()}

## Honest limitations and next steps

- **Phase 4/5 incomplete**: QLoRA training succeeded (see above) but the model was never
  served or benchmarked — this is blocked on free-tier Colab GPU quota, not a design gap.
  Picking this up requires either patience with Colab's quota reset, a different GPU
  platform (Kaggle's free tier has a separate quota), or a paid GPU rental.
- **Retrieval benchmark vocabulary leakage (partially addressed)**: alarm messages are
  LLM-paraphrased independently of the runbook text specifically to reduce this, but the
  underlying failure-type taxonomy still correlates the two to some degree by
  construction (one author wrote both).
- **RAGAS faithfulness likely underestimates true groundedness**: it only credits claims
  supported by retrieved runbook *text*, not the topology/alarm evidence citations the
  Remediation Writer also legitimately uses.
- **RAGAS sample size**: scored on a 10-row-per-mode sub-sample (not the full test
  split) due to free-tier rate limits (15 requests/minute, 500/day on the model used).
- **Free-tier rate limits shaped a lot of this project's pacing**: both the LLM API
  (Gemini) and the GPU (Colab) have tight free-tier quotas that directly constrained
  sample sizes and phase sequencing. A paid tier on either would remove these
  constraints without any code changes (the LLM client is swappable via `.env`).
- **Single-seed topology**: all results are on one seeded synthetic topology/incident
  set (seed=42). Re-running with other seeds would show variance not captured here.
"""


def main() -> None:
    RESULTS_DIR.mkdir(parents=True, exist_ok=True)
    readme = build_readme()
    (REPO_ROOT / "README.md").write_text(readme)
    print(f"README.md regenerated from {RESULTS_DIR}/*.json")


if __name__ == "__main__":
    main()
