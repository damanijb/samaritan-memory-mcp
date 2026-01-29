"""
Semantic memory storage using Qdrant vectors + Ollama embeddings.

Configurable via environment variables:
- QDRANT_URL (default: http://localhost:6333)
- OLLAMA_URL (default: http://localhost:11434)
- EMBEDDING_MODEL (default: snowflake-arctic-embed:335m)
- EMBEDDING_DIM (default: 1024)
- COLLECTION_NAME (default: samaritan_memory)
- VLLM_RERANKER_URL (default: http://localhost:8004)
- RERANKER_MODEL (default: Qwen/Qwen3-Reranker-0.6B)
"""

import os
import httpx
import math
import uuid
from datetime import datetime, timezone
from typing import Optional
from dataclasses import dataclass, asdict

# Configuration from environment
QDRANT_URL = os.environ.get("QDRANT_URL", "http://localhost:6333")
OLLAMA_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")
VLLM_RERANKER_URL = os.environ.get("VLLM_RERANKER_URL", "http://localhost:8004")
EMBEDDING_MODEL = os.environ.get("EMBEDDING_MODEL", "snowflake-arctic-embed:335m")
RERANKER_MODEL = os.environ.get("RERANKER_MODEL", "Qwen/Qwen3-Reranker-0.6B")
EMBEDDING_DIM = int(os.environ.get("EMBEDDING_DIM", "1024"))
COLLECTION_NAME = os.environ.get("COLLECTION_NAME", "samaritan_memory")


@dataclass
class Memory:
    """A memory entry."""
    id: str
    content: str
    memory_type: str  # preference, decision, insight, event, task
    timestamp: str
    importance: str = "normal"  # low, normal, high, critical
    status: str = "active"  # active, completed, superseded, archived
    source: str = "conversation"
    tags: list[str] = None
    superseded_by: str = None
    supersedes: str = None

    def __post_init__(self):
        if self.tags is None:
            self.tags = []


async def _get_embedding(text: str) -> list[float]:
    """Get embedding from Ollama."""
    async with httpx.AsyncClient(timeout=30.0) as client:
        response = await client.post(
            f"{OLLAMA_URL}/api/embeddings",
            json={"model": EMBEDDING_MODEL, "prompt": text}
        )
        response.raise_for_status()
        return response.json()["embedding"]


async def _rerank(query: str, documents: list[dict]) -> list[dict]:
    """Rerank documents using Qwen3-Reranker via vLLM."""
    if not documents:
        return documents

    instruction = "Given a query, retrieve relevant passages"
    scored_docs = []

    async with httpx.AsyncClient(timeout=30.0) as client:
        for doc in documents:
            content = doc.get("content", "")
            prompt = f"<Instruct>: {instruction}\n<Query>: {query}\n<Document>: {content}\nRelevant:"

            try:
                resp = await client.post(
                    f"{VLLM_RERANKER_URL}/v1/completions",
                    json={
                        "model": RERANKER_MODEL,
                        "prompt": prompt,
                        "max_tokens": 1,
                        "temperature": 0,
                        "logprobs": 20
                    }
                )

                if resp.status_code != 200:
                    scored_docs.append((doc.get("score", 0.5), doc))
                    continue

                data = resp.json()
                top_logprobs = data["choices"][0]["logprobs"]["top_logprobs"][0]

                yes_logprob = None
                no_logprob = None
                for token, logprob in top_logprobs.items():
                    token_lower = token.lower().strip()
                    if token_lower in ["yes", "yes,", "yes."]:
                        yes_logprob = logprob
                    elif token_lower in ["no", "no,", "no."]:
                        no_logprob = logprob

                if yes_logprob is not None and no_logprob is not None:
                    yes_exp = math.exp(yes_logprob)
                    no_exp = math.exp(no_logprob)
                    score = yes_exp / (yes_exp + no_exp)
                elif yes_logprob is not None:
                    score = 0.9
                elif no_logprob is not None:
                    score = 0.1
                else:
                    score = doc.get("score", 0.5)

                scored_docs.append((score, doc))

            except Exception:
                scored_docs.append((doc.get("score", 0.5), doc))

    scored_docs.sort(key=lambda x: x[0], reverse=True)

    result = []
    for rerank_score, doc in scored_docs:
        doc = doc.copy()
        doc["rerank_score"] = rerank_score
        result.append(doc)

    return result


async def _ensure_collection():
    """Create collection if it doesn't exist."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{QDRANT_URL}/collections/{COLLECTION_NAME}")
        if response.status_code == 200:
            return

        await client.put(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}",
            json={
                "vectors": {
                    "size": EMBEDDING_DIM,
                    "distance": "Cosine"
                }
            }
        )


async def find_similar_memory(
    content: str,
    similarity_threshold: float = 0.85,
    memory_type: Optional[str] = None,
) -> Optional[dict]:
    """Find existing memory similar to the given content."""
    await _ensure_collection()
    embedding = await _get_embedding(content)

    filter_conditions = [{"key": "status", "match": {"value": "active"}}]
    if memory_type:
        filter_conditions.append({"key": "memory_type", "match": {"value": memory_type}})

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search",
            json={
                "vector": embedding,
                "limit": 1,
                "score_threshold": similarity_threshold,
                "filter": {"must": filter_conditions},
                "with_payload": True,
            }
        )
        if response.status_code != 200:
            return None

        results = response.json()["result"]

    if results:
        return {
            "id": results[0]["payload"]["id"],
            "score": results[0]["score"],
            **results[0]["payload"]
        }
    return None


async def add_memory(
    content: str,
    memory_type: str = "insight",
    importance: str = "normal",
    source: str = "conversation",
    tags: list[str] = None,
    supersedes: str = None,
    deduplicate: bool = True,
    similarity_threshold: float = 0.85,
) -> str:
    """Add a memory with automatic deduplication."""
    await _ensure_collection()

    if deduplicate and not supersedes:
        existing = await find_similar_memory(content, similarity_threshold, memory_type)
        if existing:
            existing_id = existing["id"]
            await update_memory(
                existing_id,
                content=content,
                importance=importance if importance != "normal" else existing.get("importance", importance),
            )
            return existing_id

    memory_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()

    memory = Memory(
        id=memory_id,
        content=content,
        memory_type=memory_type,
        timestamp=timestamp,
        importance=importance,
        status="active",
        source=source,
        tags=tags or [],
        supersedes=supersedes,
    )

    embedding = await _get_embedding(content)

    async with httpx.AsyncClient() as client:
        await client.put(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points",
            json={
                "points": [{
                    "id": memory_id,
                    "vector": embedding,
                    "payload": asdict(memory)
                }]
            }
        )

    if supersedes:
        await update_memory(supersedes, status="superseded", superseded_by=memory_id)

    return memory_id


async def update_memory(
    memory_id: str,
    content: str = None,
    status: str = None,
    importance: str = None,
    superseded_by: str = None,
) -> bool:
    """Update a memory's fields."""
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/scroll",
            json={
                "filter": {"must": [{"key": "id", "match": {"value": memory_id}}]},
                "limit": 1,
                "with_payload": True,
                "with_vector": True,
            }
        )

        if response.status_code != 200:
            return False

        points = response.json()["result"]["points"]
        if not points:
            return False

        point = points[0]
        payload = point["payload"]
        vector = point["vector"]

        if content is not None:
            payload["content"] = content
            vector = await _get_embedding(content)
        if status is not None:
            payload["status"] = status
        if importance is not None:
            payload["importance"] = importance
        if superseded_by is not None:
            payload["superseded_by"] = superseded_by

        payload["timestamp"] = datetime.now(timezone.utc).isoformat()

        await client.put(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points",
            json={
                "points": [{
                    "id": memory_id,
                    "vector": vector,
                    "payload": payload
                }]
            }
        )

    return True


async def search_memory(
    query: str,
    limit: int = 5,
    memory_type: Optional[str] = None,
    importance: Optional[str] = None,
    status: str = "active",
    min_score: float = 0.5,
    rerank: bool = True,
) -> list[dict]:
    """Search memories by semantic similarity with optional reranking."""
    await _ensure_collection()

    embedding = await _get_embedding(query)

    filter_conditions = []
    if status:
        filter_conditions.append({"key": "status", "match": {"value": status}})
    if memory_type:
        filter_conditions.append({"key": "memory_type", "match": {"value": memory_type}})
    if importance:
        filter_conditions.append({"key": "importance", "match": {"value": importance}})

    search_filter = {"must": filter_conditions} if filter_conditions else None
    fetch_limit = limit * 3 if rerank else limit

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/search",
            json={
                "vector": embedding,
                "limit": fetch_limit,
                "score_threshold": min_score,
                "filter": search_filter,
                "with_payload": True,
            }
        )
        response.raise_for_status()
        results = response.json()["result"]

    candidates = [{"score": r["score"], **r["payload"]} for r in results]

    if rerank and candidates:
        try:
            candidates = await _rerank(query, candidates)
        except Exception:
            pass

    return candidates[:limit]


async def get_recent_memories(
    limit: int = 10,
    memory_type: Optional[str] = None,
) -> list[dict]:
    """Get most recent memories."""
    await _ensure_collection()

    filter_conditions = []
    if memory_type:
        filter_conditions.append({"key": "memory_type", "match": {"value": memory_type}})

    search_filter = {"must": filter_conditions} if filter_conditions else None

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{QDRANT_URL}/collections/{COLLECTION_NAME}/points/scroll",
            json={
                "limit": limit,
                "filter": search_filter,
                "with_payload": True,
                "order_by": {"key": "timestamp", "direction": "desc"}
            }
        )
        response.raise_for_status()
        results = response.json()["result"]["points"]

    return [r["payload"] for r in results]


async def get_memory_stats() -> dict:
    """Get memory collection statistics."""
    async with httpx.AsyncClient() as client:
        response = await client.get(f"{QDRANT_URL}/collections/{COLLECTION_NAME}")
        if response.status_code != 200:
            return {"count": 0, "status": "not_initialized"}

        data = response.json()["result"]
        return {
            "count": data.get("points_count", 0),
            "status": data.get("status", "unknown"),
        }
