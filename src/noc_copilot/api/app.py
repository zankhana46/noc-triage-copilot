"""FastAPI backend: POST /triage runs an alarm window through the full
LangGraph pipeline and returns the structured TriageReport (SPEC.md 6.2).

Run with: uv run --extra dev uvicorn noc_copilot.api.app:app --reload
"""

from __future__ import annotations

import os
import uuid
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from noc_copilot.agents.graph import build_triage_graph
from noc_copilot.agents.pipeline import run_triage
from noc_copilot.graph.loader import get_driver
from noc_copilot.retrieval.hybrid import build_retriever

load_dotenv()
REPO_ROOT = Path(__file__).resolve().parents[3]

_state: dict[str, Any] = {}


@asynccontextmanager
async def lifespan(_: FastAPI) -> AsyncIterator[None]:
    driver = get_driver(
        os.environ.get("NEO4J_URI", "bolt://localhost:7687"),
        os.environ.get("NEO4J_USER", "neo4j"),
        os.environ["NEO4J_PASSWORD"],
    )
    retrieval_cfg = yaml.safe_load((REPO_ROOT / "configs" / "retrieval.yaml").read_text())
    retriever = build_retriever(retrieval_cfg, REPO_ROOT / "data" / "runbooks", REPO_ROOT)
    _state["driver"] = driver
    top_k = retrieval_cfg.get("top_k", 5)
    _state["graph"] = build_triage_graph(driver, retriever, retrieval_top_k=top_k)
    yield
    driver.close()
    _state.clear()


app = FastAPI(title="NOC Triage Copilot", lifespan=lifespan)


class AlarmIn(BaseModel):
    alarm_id: str | None = None
    timestamp: str
    node_id: str
    alarm_type: str
    severity: str
    vendor_code: str = "UNK"
    message: str


class TriageRequest(BaseModel):
    alarms: list[AlarmIn]
    use_graph_tools: bool = True
    retrieval_mode: str = "hybrid+rerank"


@app.get("/health")
def health() -> dict[str, str]:
    return {"status": "ok"}


@app.post("/triage")
def triage(request: TriageRequest) -> dict[str, Any]:
    if not request.alarms:
        raise HTTPException(status_code=400, detail="alarms must be non-empty")

    alarm_dicts = []
    for alarm in request.alarms:
        d = alarm.model_dump()
        d["alarm_id"] = d["alarm_id"] or str(uuid.uuid4())
        alarm_dicts.append(d)

    return run_triage(
        _state["graph"],
        alarm_dicts,
        use_graph_tools=request.use_graph_tools,
        retrieval_mode=request.retrieval_mode,
    )
