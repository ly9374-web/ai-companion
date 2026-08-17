"""Persistent hybrid retrieval for character-scoped long-term memories."""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import chromadb
import jieba
import numpy as np
from chromadb.config import Settings
from loguru import logger
from rank_bm25 import BM25Okapi
from sentence_transformers import SentenceTransformer


PROJECT_ROOT = Path(__file__).resolve().parents[2]
MODEL_PATH = PROJECT_ROOT / "models" / "rag_embedding_model"
CHROMA_PATH = PROJECT_ROOT / "cache" / "rag" / "chroma"
COLLECTION_NAME = "long_term_memories"
CANDIDATE_MULTIPLIER = 4
RRF_K = 60

jieba.setLogLevel(logging.WARNING)
_CJK_RE = re.compile(r"[\u3400-\u4dbf\u4e00-\u9fff\uf900-\ufaff]")


@dataclass(frozen=True)
class RagMemory:
    count: int
    content: str
    type: str
    reference: str
    created_at: str = ""
    updated_at: str = ""
    source_batch_id: str = ""

    @property
    def search_text(self) -> str:
        return " ".join(
            value.strip()
            for value in (self.content, self.reference, self.type)
            if value.strip()
        )


def _tokenize(text: str) -> list[str]:
    if _CJK_RE.search(text):
        return [token.casefold() for token in jieba.lcut(text) if token.strip()]
    return [
        token.casefold()
        for token in re.split(r"[^\w]+", text, flags=re.UNICODE)
        if token
    ]


def _memory_id(conf_uid: str, count: int) -> str:
    digest = hashlib.sha256(conf_uid.encode("utf-8")).hexdigest()[:16]
    return f"{digest}:{count:06d}"


class MemoryRagStore:
    """Own the local embedding model and a rebuildable Chroma collection."""

    def __init__(self) -> None:
        self._model: SentenceTransformer | None = None
        self._client: chromadb.PersistentClient | None = None
        self._collection = None
        self._lock = threading.RLock()

    def _get_model(self) -> SentenceTransformer:
        if self._model is None:
            if not MODEL_PATH.is_dir():
                raise FileNotFoundError(
                    f"Bundled RAG embedding model is missing: {MODEL_PATH}"
                )
            logger.info("Loading bundled RAG embedding model from {}", MODEL_PATH)
            self._model = SentenceTransformer(
                str(MODEL_PATH),
                local_files_only=True,
            )
        return self._model

    def _embed(self, texts: list[str]) -> list[list[float]]:
        if not texts:
            return []
        vectors = self._get_model().encode(
            texts,
            normalize_embeddings=True,
            show_progress_bar=False,
        )
        return np.asarray(vectors, dtype=np.float32).tolist()

    def _get_collection(self):
        if self._collection is None:
            CHROMA_PATH.mkdir(parents=True, exist_ok=True)
            self._client = chromadb.PersistentClient(
                path=str(CHROMA_PATH),
                settings=Settings(anonymized_telemetry=False),
            )
            self._collection = self._client.get_or_create_collection(
                COLLECTION_NAME,
                metadata={"hnsw:space": "cosine"},
            )
        return self._collection

    @staticmethod
    def _metadata(conf_uid: str, memory: RagMemory) -> dict[str, object]:
        fingerprint_source = json.dumps(
            {
                "search_text": memory.search_text,
                "created_at": memory.created_at,
                "updated_at": memory.updated_at,
                "source_batch_id": memory.source_batch_id,
            },
            ensure_ascii=False,
            sort_keys=True,
        )
        return {
            "conf_uid": conf_uid,
            "count": memory.count,
            "content": memory.content,
            "type": memory.type,
            "reference": memory.reference,
            "created_at": memory.created_at,
            "updated_at": memory.updated_at,
            "source_batch_id": memory.source_batch_id,
            "fingerprint": hashlib.sha256(
                fingerprint_source.encode("utf-8")
            ).hexdigest(),
        }

    def sync(self, conf_uid: str, memories: Iterable[RagMemory]) -> None:
        """Incrementally make one character's vector rows match its Markdown."""
        with self._lock:
            normalized = list(memories)
            collection = self._get_collection()
            existing = collection.get(
                where={"conf_uid": conf_uid},
                include=["metadatas"],
            )
            existing_meta = {
                memory_id: metadata
                for memory_id, metadata in zip(
                    existing.get("ids", []), existing.get("metadatas", [])
                )
            }
            desired = {
                _memory_id(conf_uid, memory.count): memory for memory in normalized
            }

            stale_ids = [
                memory_id for memory_id in existing_meta if memory_id not in desired
            ]
            if stale_ids:
                collection.delete(ids=stale_ids)

            changed: list[tuple[str, RagMemory, dict[str, object]]] = []
            for memory_id, memory in desired.items():
                metadata = self._metadata(conf_uid, memory)
                if (
                    existing_meta.get(memory_id, {}).get("fingerprint")
                    != metadata["fingerprint"]
                ):
                    changed.append((memory_id, memory, metadata))

            if changed:
                embeddings = self._embed(
                    [memory.search_text for _, memory, _ in changed]
                )
                collection.upsert(
                    ids=[memory_id for memory_id, _, _ in changed],
                    embeddings=embeddings,
                    documents=[memory.search_text for _, memory, _ in changed],
                    metadatas=[metadata for _, _, metadata in changed],
                )
                logger.info(
                    "Updated {} RAG rows for character {}",
                    len(changed),
                    conf_uid,
                )

    def retrieve(
        self,
        conf_uid: str,
        query: str,
        memories: Iterable[RagMemory],
        *,
        top_k: int,
        threshold: float,
        hybrid_weight: float,
    ) -> list[RagMemory]:
        results = self.retrieve_many(
            conf_uid,
            [query],
            memories,
            top_k=top_k,
            threshold=threshold,
            hybrid_weight=hybrid_weight,
        )
        return results[0] if results else []

    def retrieve_many(
        self,
        conf_uid: str,
        queries: Iterable[str],
        memories: Iterable[RagMemory],
        *,
        top_k: int,
        threshold: float,
        hybrid_weight: float,
    ) -> list[list[RagMemory]]:
        query_list = [query for query in queries]
        normalized = list(memories)
        if not query_list:
            return []
        if not normalized:
            return [[] for _ in query_list]

        alpha = min(1.0, max(0.0, hybrid_weight))
        tokenized_queries = [_tokenize(query) for query in query_list]
        tokenized_documents = [_tokenize(memory.search_text) for memory in normalized]

        # A zero vector weight is a genuinely keyword-only path: it does not
        # load the embedding model, access Chroma, or apply the vector threshold.
        if alpha == 0.0:
            if not any(tokenized_documents):
                return [[] for _ in query_list]
            bm25 = BM25Okapi(tokenized_documents)
            results: list[list[RagMemory]] = []
            for tokenized_query in tokenized_queries:
                if not tokenized_query:
                    results.append([])
                    continue
                bm25_scores = bm25.get_scores(tokenized_query)
                ranked_indices = [
                    index
                    for index in np.argsort(bm25_scores)[::-1]
                    if bm25_scores[index] > 0
                ]
                results.append(
                    [normalized[index] for index in ranked_indices[:top_k]]
                )
            return results

        with self._lock:
            self.sync(conf_uid, normalized)
            collection = self._get_collection()
            stored = collection.get(
                where={"conf_uid": conf_uid},
                include=["embeddings", "metadatas"],
            )

            ids = list(stored.get("ids", []))
            if not ids:
                return [[] for _ in query_list]
            metadatas = list(stored.get("metadatas", []))
            embeddings = np.asarray(stored.get("embeddings", []), dtype=np.float32)
            query_embeddings = np.asarray(
                self._embed(query_list), dtype=np.float32
            )

            documents = [
                RagMemory(
                    count=int(metadata.get("count", 0)),
                    content=str(metadata.get("content", "")),
                    type=str(metadata.get("type", "")),
                    reference=str(metadata.get("reference", "")),
                    created_at=str(metadata.get("created_at", "")),
                    updated_at=str(metadata.get("updated_at", "")),
                    source_batch_id=str(metadata.get("source_batch_id", "")),
                )
                for metadata in metadatas
            ]
            by_id = dict(zip(ids, documents))

        tokenized_documents = [_tokenize(memory.search_text) for memory in documents]

        candidate_k = min(len(ids), max(top_k, top_k * CANDIDATE_MULTIPLIER))
        bm25 = BM25Okapi(tokenized_documents) if any(tokenized_documents) else None
        all_results: list[list[RagMemory]] = []
        for query_index, tokenized_query in enumerate(tokenized_queries):
            similarities = embeddings @ query_embeddings[query_index]
            similarity_by_id = {
                memory_id: float(score)
                for memory_id, score in zip(ids, similarities)
            }
            vector_ids = [
                memory_id
                for memory_id in sorted(
                    ids,
                    key=lambda candidate_id: similarity_by_id[candidate_id],
                    reverse=True,
                )
                if similarity_by_id[memory_id] >= threshold
            ][:candidate_k]

            if bm25 is not None and tokenized_query:
                bm25_scores = bm25.get_scores(tokenized_query)
                bm25_ids = [
                    ids[index]
                    for index in np.argsort(bm25_scores)[::-1][:candidate_k]
                    if bm25_scores[index] > 0
                ]
            else:
                bm25_ids = []

            if alpha == 1.0:
                ranked_ids = vector_ids
            else:
                scores: dict[str, float] = {}
                for rank, memory_id in enumerate(vector_ids, start=1):
                    scores[memory_id] = alpha / (RRF_K + rank)
                for rank, memory_id in enumerate(bm25_ids, start=1):
                    scores[memory_id] = scores.get(memory_id, 0.0) + (
                        (1.0 - alpha) / (RRF_K + rank)
                    )
                ranked_ids = sorted(scores, key=scores.__getitem__, reverse=True)

            all_results.append(
                [by_id[memory_id] for memory_id in ranked_ids[:top_k]]
            )
        return all_results


memory_rag_store = MemoryRagStore()
