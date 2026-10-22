"""Hybrid runbook retrieval: BM25 + dense (Sentence-Transformers embeddings
in ChromaDB), merged with reciprocal rank fusion, optionally reranked with
a cross-encoder. The retrieval `mode` is configurable so each stage can be
ablated independently (see configs/retrieval.yaml and scripts/02_evaluate_retrieval.py).
"""

from __future__ import annotations

import re
from pathlib import Path

import chromadb
from rank_bm25 import BM25Okapi
from sentence_transformers import CrossEncoder, SentenceTransformer

from noc_copilot.retrieval.chunking import RunbookChunk, load_runbook_chunks

MODES = ("bm25", "dense", "hybrid", "hybrid+rerank")


def _tokenize(text: str) -> list[str]:
    return re.findall(r"[a-z0-9]+", text.lower())


class HybridRetriever:
    def __init__(self, chunks: list[RunbookChunk], cfg: dict, persist_dir: Path):
        if not chunks:
            raise ValueError("No runbook chunks to index")
        self.chunks = chunks
        self.rrf_k = cfg["rrf_k"]
        self.rerank_pool_size = cfg["rerank_pool_size"]

        self._bm25 = BM25Okapi([_tokenize(c.text) for c in chunks])
        self._embed_model = SentenceTransformer(cfg["embedding_model"])
        self._cross_encoder = CrossEncoder(cfg["cross_encoder_model"])

        persist_dir.mkdir(parents=True, exist_ok=True)
        client = chromadb.PersistentClient(path=str(persist_dir))
        # rebuild the collection fresh each run: the runbook corpus is tiny
        # (~80 chunks) so re-embedding is cheap, and this keeps the index
        # from drifting out of sync with whatever's on disk in data/runbooks/.
        try:
            client.delete_collection(cfg["chroma_collection"])
        except Exception:
            pass
        self._collection = client.create_collection(cfg["chroma_collection"])
        embeddings = self._embed_model.encode(
            [c.text for c in chunks], normalize_embeddings=True
        ).tolist()
        self._collection.add(
            ids=[c.chunk_id for c in chunks],
            embeddings=embeddings,
            documents=[c.text for c in chunks],
            metadatas=[{"runbook_id": c.runbook_id, "section": c.section} for c in chunks],
        )

    def _rank_bm25(self, query: str) -> list[str]:
        scores = self._bm25.get_scores(_tokenize(query))
        order = sorted(range(len(self.chunks)), key=lambda i: scores[i], reverse=True)
        return [self.chunks[i].chunk_id for i in order]

    def _rank_dense(self, query: str) -> list[str]:
        q_embedding = self._embed_model.encode([query], normalize_embeddings=True).tolist()
        result = self._collection.query(query_embeddings=q_embedding, n_results=len(self.chunks))
        return result["ids"][0]

    def _rrf_merge(self, rankings: list[list[str]]) -> list[str]:
        scores: dict[str, float] = {}
        for ranking in rankings:
            for rank, chunk_id in enumerate(ranking):
                scores[chunk_id] = scores.get(chunk_id, 0.0) + 1.0 / (self.rrf_k + rank + 1)
        return sorted(scores, key=lambda cid: scores[cid], reverse=True)

    def _rerank(self, query: str, chunk_ids: list[str]) -> list[str]:
        by_id = {c.chunk_id: c for c in self.chunks}
        pairs = [(query, by_id[cid].text) for cid in chunk_ids]
        scores = self._cross_encoder.predict(pairs)
        ranked = sorted(zip(scores, chunk_ids, strict=True), key=lambda x: x[0], reverse=True)
        return [cid for _, cid in ranked]

    def retrieve(self, query: str, mode: str, top_k: int) -> list[RunbookChunk]:
        if mode not in MODES:
            raise ValueError(f"unknown retrieval mode {mode!r}; expected one of {MODES}")

        if mode == "bm25":
            ranked_ids = self._rank_bm25(query)
        elif mode == "dense":
            ranked_ids = self._rank_dense(query)
        else:  # hybrid, hybrid+rerank
            ranked_ids = self._rrf_merge([self._rank_bm25(query), self._rank_dense(query)])

        if mode == "hybrid+rerank":
            pool = ranked_ids[: self.rerank_pool_size]
            ranked_ids = self._rerank(query, pool)

        by_id = {c.chunk_id: c for c in self.chunks}
        return [by_id[cid] for cid in ranked_ids[:top_k]]


def build_retriever(cfg: dict, runbooks_dir: Path, repo_root: Path) -> HybridRetriever:
    chunks = load_runbook_chunks(runbooks_dir)
    return HybridRetriever(chunks, cfg, repo_root / cfg["chroma_persist_dir"])
