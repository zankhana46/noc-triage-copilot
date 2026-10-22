"""One OpenAI-compatible client for everything.

Points at whatever LLM_BASE_URL / LLM_MODEL / LLM_API_KEY in the environment
say — Gemini's free-tier OpenAI-compatible endpoint by default, or a local
vLLM server in Phase 5 by swapping those three env vars. Nothing in the
rest of the codebase should import `openai` directly; go through this
module so the swap stays a config change, not a code change.
"""

from __future__ import annotations

import os
import time

from dotenv import load_dotenv
from openai import APIConnectionError, InternalServerError, OpenAI, RateLimitError
from openai.types.chat import ChatCompletion, ChatCompletionMessageParam
from pydantic import BaseModel

load_dotenv()

# Free-tier Gemini models return transient 503s under high demand fairly
# often; the SDK's own retries (max_retries=5 below) aren't always enough,
# so `chat()` also retries with backoff at the application level.
_RETRYABLE_ERRORS = (RateLimitError, InternalServerError, APIConnectionError)
_MAX_RETRIES = 4
_BACKOFF_SECONDS = 5


def get_client(base_url: str | None = None, api_key: str | None = None) -> OpenAI:
    """Return an OpenAI-compatible client configured from the environment."""
    base_url = base_url or os.environ.get("LLM_BASE_URL")
    api_key = api_key or os.environ.get("LLM_API_KEY")
    if not base_url or not api_key:
        raise RuntimeError(
            "LLM_BASE_URL and LLM_API_KEY must be set (see .env.example). "
            "Copy .env.example to .env and fill in a free Gemini API key "
            "from https://aistudio.google.com/apikey."
        )
    return OpenAI(base_url=base_url, api_key=api_key, max_retries=5)


def get_model(default: str | None = None) -> str:
    model = os.environ.get("LLM_MODEL", default)
    if not model:
        raise RuntimeError("LLM_MODEL must be set (see .env.example).")
    return model


def get_paraphrase_model() -> str:
    return os.environ.get("LLM_PARAPHRASE_MODEL") or get_model()


def chat_response(
    messages: list[ChatCompletionMessageParam],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    response_format: dict | None = None,
    client: OpenAI | None = None,
) -> ChatCompletion:
    """Single non-streaming chat completion; returns the raw response (so
    callers that need usage/token counts for telemetry can get at them)."""
    client = client or get_client()
    model = model or get_model()
    kwargs: dict = {}
    if response_format is not None:
        kwargs["response_format"] = response_format

    for attempt in range(_MAX_RETRIES + 1):
        try:
            return client.chat.completions.create(
                model=model,
                messages=messages,
                temperature=temperature,
                **kwargs,
            )
        except _RETRYABLE_ERRORS:
            if attempt == _MAX_RETRIES:
                raise
            time.sleep(_BACKOFF_SECONDS * (2**attempt))
    raise AssertionError("unreachable")  # loop always returns or raises


_MOCK_RUNBOOK_BODY = (
    "## Symptoms\n- Mock symptom for CI.\n\n"
    "## Probable causes\n- Mock cause for CI.\n\n"
    "## Diagnostic steps\n- Mock diagnostic step for CI.\n\n"
    "## Remediation\n- Mock remediation step for CI.\n\n"
    "## Escalation\n- Mock escalation step for CI.\n"
)


def _mock_chat(messages: list[ChatCompletionMessageParam]) -> str:
    """Deterministic canned response, used when MOCK_LLM=1 (set in CI — see
    .github/workflows/ci.yml) so the tiny-dataset generation scripts run
    with no real API calls. Only `chat()` needs this: Phase 1's data
    generation (the only thing CI's tiny dataset exercises) never calls
    chat_structured()."""
    system = messages[0].get("content", "") if messages else ""
    if isinstance(system, str) and "JSON object mapping" in system:
        # alarm paraphrase cache request: alarms.py already falls back to
        # its deterministic templates for any alarm_type an empty/invalid
        # response doesn't cover, so "{}" is a valid, sufficient mock.
        return "{}"
    return _MOCK_RUNBOOK_BODY


def chat(
    messages: list[ChatCompletionMessageParam],
    *,
    model: str | None = None,
    temperature: float = 0.7,
    response_format: dict | None = None,
    client: OpenAI | None = None,
) -> str:
    """Single non-streaming chat completion; returns the message content."""
    if os.environ.get("MOCK_LLM") == "1":
        return _mock_chat(messages)
    response = chat_response(
        messages,
        model=model,
        temperature=temperature,
        response_format=response_format,
        client=client,
    )
    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError(f"LLM returned no content: {response}")
    return content


def chat_structured[ModelT: BaseModel](
    messages: list[ChatCompletionMessageParam],
    schema: type[ModelT],
    *,
    model: str | None = None,
    temperature: float = 0.0,
    client: OpenAI | None = None,
) -> tuple[ModelT, ChatCompletion]:
    """Chat completion constrained to `schema` via strict JSON-schema mode,
    parsed and validated into a `schema` instance. Returns (parsed,
    raw_response) so callers can also log token usage for telemetry."""
    json_schema = {"name": schema.__name__, "schema": schema.model_json_schema(), "strict": True}
    response_format = {"type": "json_schema", "json_schema": json_schema}
    response = chat_response(
        messages,
        model=model,
        temperature=temperature,
        response_format=response_format,
        client=client,
    )
    content = response.choices[0].message.content
    if content is None:
        raise RuntimeError(f"LLM returned no content: {response}")
    return schema.model_validate_json(content), response
