#!/usr/bin/env python3
"""Phase 3: computes RAGAS metrics (faithfulness, answer_relevancy,
context_precision, context_recall) from results/ragas_input.jsonl
(written by scripts/03_evaluate.py), grouped by retrieval mode. Writes
results/ragas_eval.json.

Must run in the isolated eval venv — ragas's langchain-openai/instructor
transitive deps conflict with langgraph's in the main project environment
(see requirements-eval.txt for why, and the exact pins needed):
    uv venv .venv-eval && uv pip install -r requirements-eval.txt --python .venv-eval
    uv run --python .venv-eval scripts/03b_ragas_eval.py [--limit N]

This script intentionally does not import anything from noc_copilot: the
isolated venv doesn't have neo4j/networkx/langgraph installed, only what
requirements-eval.txt lists.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import re
from collections import defaultdict
from collections.abc import Awaitable, Callable
from pathlib import Path

from dotenv import load_dotenv
from openai import AsyncOpenAI
from ragas.embeddings import HuggingFaceEmbeddings
from ragas.llms import llm_factory
from ragas.metrics.collections import (
    AnswerRelevancy,
    ContextPrecision,
    ContextRecall,
    Faithfulness,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
EMBEDDING_MODEL = "sentence-transformers/all-MiniLM-L6-v2"

# Free-tier Gemini caps gemini-3.5-flash-lite at 15 requests/minute; ragas's
# own retry logic gives up after a single attempt on a 429, so retry with
# backoff here instead (honoring the server's suggested "retry in Ns" delay
# when the error message includes one).
_MAX_RETRIES = 6
_DEFAULT_BACKOFF_SECONDS = 5.0
_RETRY_DELAY_RE = re.compile(r"retry in ([\d.]+)s")

async def _with_retry[T](coro_factory: Callable[[], Awaitable[T]]) -> T:
    for attempt in range(_MAX_RETRIES + 1):
        try:
            return await coro_factory()
        except Exception as e:
            is_rate_limit = "429" in str(e) or "RESOURCE_EXHAUSTED" in str(e)
            if not is_rate_limit or attempt == _MAX_RETRIES:
                raise
            match = _RETRY_DELAY_RE.search(str(e))
            if match:
                delay = float(match.group(1)) + 1.0
            else:
                delay = _DEFAULT_BACKOFF_SECONDS * (2**attempt)
            print(f"    rate limited, retrying in {delay:.0f}s ({attempt + 1}/{_MAX_RETRIES})")
            await asyncio.sleep(delay)
    raise AssertionError("unreachable")  # loop always returns or raises


async def score_row(metrics: dict, row: dict) -> dict:
    question, contexts = row["question"], row["contexts"]
    answer, reference = row["answer"], row["reference"]
    if not question.strip() or not contexts:
        return dict.fromkeys(
            ("faithfulness", "answer_relevancy", "context_precision", "context_recall")
        )

    faithfulness = await _with_retry(
        lambda: metrics["faithfulness"].ascore(
            user_input=question, response=answer, retrieved_contexts=contexts
        )
    )
    answer_relevancy = await _with_retry(
        lambda: metrics["answer_relevancy"].ascore(user_input=question, response=answer)
    )
    context_precision = await _with_retry(
        lambda: metrics["context_precision"].ascore(
            user_input=question, reference=reference, retrieved_contexts=contexts
        )
    )
    context_recall = await _with_retry(
        lambda: metrics["context_recall"].ascore(
            user_input=question, retrieved_contexts=contexts, reference=reference
        )
    )
    return {
        "faithfulness": faithfulness.value,
        "answer_relevancy": answer_relevancy.value,
        "context_precision": context_precision.value,
        "context_recall": context_recall.value,
    }


def _mean(values: list[float | None]) -> float | None:
    clean = [v for v in values if v is not None]
    return sum(clean) / len(clean) if clean else None


async def main_async(limit: int | None) -> None:
    load_dotenv(REPO_ROOT / ".env")
    input_path = REPO_ROOT / "results" / "ragas_input.jsonl"
    if not input_path.exists():
        raise SystemExit("results/ragas_input.jsonl not found; run scripts/03_evaluate.py first")

    rows = [json.loads(line) for line in input_path.read_text().splitlines() if line.strip()]
    by_mode: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        by_mode[row["mode"]].append(row)
    if limit:
        by_mode = {mode: mode_rows[:limit] for mode, mode_rows in by_mode.items()}

    client = AsyncOpenAI(base_url=os.environ["LLM_BASE_URL"], api_key=os.environ["LLM_API_KEY"])
    # gemini-3.5-flash-lite spends some of max_tokens on hidden reasoning
    # tokens even for short outputs; ragas's own default is too small for
    # that and truncates mid-response, so raise it here.
    llm = llm_factory(os.environ["LLM_MODEL"], client=client, max_tokens=4096)
    embeddings = HuggingFaceEmbeddings(model=EMBEDDING_MODEL)
    metrics = {
        "faithfulness": Faithfulness(llm=llm),
        "answer_relevancy": AnswerRelevancy(llm=llm, embeddings=embeddings),
        "context_precision": ContextPrecision(llm=llm),
        "context_recall": ContextRecall(llm=llm),
    }

    results: dict[str, dict] = {}
    for mode, mode_rows in by_mode.items():
        print(f"Scoring {len(mode_rows)} rows for mode={mode}...")
        per_row = []
        for i, row in enumerate(mode_rows):
            try:
                per_row.append(await score_row(metrics, row))
            except Exception as e:
                print(f"  row {i} failed: {e}")
            if (i + 1) % 10 == 0:
                print(f"  {i + 1}/{len(mode_rows)}")
        results[mode] = {
            "n_rows": len(per_row),
            "faithfulness": _mean([r["faithfulness"] for r in per_row]),
            "answer_relevancy": _mean([r["answer_relevancy"] for r in per_row]),
            "context_precision": _mean([r["context_precision"] for r in per_row]),
            "context_recall": _mean([r["context_recall"] for r in per_row]),
        }

    out_path = REPO_ROOT / "results" / "ragas_eval.json"
    out_path.write_text(json.dumps(results, indent=2))
    print(json.dumps(results, indent=2))
    print(f"\nWritten to {out_path}")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--limit", type=int, default=None, help="cap rows scored per mode")
    args = parser.parse_args()
    asyncio.run(main_async(args.limit))


if __name__ == "__main__":
    main()
