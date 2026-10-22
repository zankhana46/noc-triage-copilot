"""Chunks runbook Markdown files by H2 section for retrieval."""

from __future__ import annotations

import re
from pathlib import Path

from pydantic import BaseModel

_SECTION_SPLIT_RE = re.compile(r"\n(?=## )")
_SECTION_HEADER_RE = re.compile(r"^## (.+)$", re.MULTILINE)


class RunbookChunk(BaseModel):
    chunk_id: str
    runbook_id: str
    section: str
    text: str


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")


def chunk_runbook(runbook_id: str, markdown: str) -> list[RunbookChunk]:
    chunks = []
    for block in _SECTION_SPLIT_RE.split(markdown):
        block = block.strip()
        header = _SECTION_HEADER_RE.match(block)
        if not header:
            continue  # the leading "# Title" block has no "## " header
        section = header.group(1).strip()
        chunks.append(
            RunbookChunk(
                chunk_id=f"{runbook_id}::{_slug(section)}",
                runbook_id=runbook_id,
                section=section,
                text=block,
            )
        )
    return chunks


def load_runbook_chunks(runbooks_dir: Path) -> list[RunbookChunk]:
    chunks: list[RunbookChunk] = []
    for md_path in sorted(runbooks_dir.glob("*.md")):
        chunks.extend(chunk_runbook(md_path.stem, md_path.read_text()))
    return chunks
