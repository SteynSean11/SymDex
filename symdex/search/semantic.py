# Copyright (c) 2026 Muhammad Husnain
# License: See LICENSE file in the project root.

import os
import json
import urllib.request
import numpy as np

_model = None


def _get_model():
    global _model
    if _model is None:
        from sentence_transformers import SentenceTransformer
        model_name = os.environ.get("SYMDEX_EMBED_MODEL", "all-MiniLM-L6-v2")
        _model = SentenceTransformer(model_name)
    return _model


def embed_text(text: str) -> np.ndarray:
    """Return float32 embedding vector for text."""
    backend = os.environ.get("SYMDEX_EMBED_BACKEND", "local")
    if backend == "claude":
        return _embed_claude(text)
    if backend == "ollama":
        return _embed_ollama(text)
    model = _get_model()
    vec = model.encode(text, normalize_embeddings=True)
    return vec.astype("float32")


def _embed_claude(text: str) -> np.ndarray:
    import anthropic
    client = anthropic.Anthropic()
    response = client.embeddings.create(
        model="voyage-code-2",
        input=[text],
    )
    return np.array(response.embeddings[0].embedding, dtype="float32")


def _embed_ollama(text: str) -> np.ndarray:
    """Embed using Ollama's local HTTP API."""
    base_url = os.environ.get("SYMDEX_OLLAMA_URL", "http://127.0.0.1:11434").rstrip("/")
    model = os.environ.get("SYMDEX_OLLAMA_MODEL", "qwen3-embedding:0.6b")
    timeout = float(os.environ.get("SYMDEX_OLLAMA_TIMEOUT", "30"))
    headers = {"Content-Type": "application/json"}

    # Try legacy endpoint first: /api/embeddings
    payload_legacy = json.dumps({"model": model, "prompt": text}).encode("utf-8")
    req_legacy = urllib.request.Request(
        url=f"{base_url}/api/embeddings",
        data=payload_legacy,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req_legacy, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            if "embedding" in data:
                return _validate_embedding_dim(data["embedding"])
    except Exception:
        pass

    # Fallback endpoint: /api/embed
    payload_new = json.dumps({"model": model, "input": text}).encode("utf-8")
    req_new = urllib.request.Request(
        url=f"{base_url}/api/embed",
        data=payload_new,
        headers=headers,
        method="POST",
    )
    with urllib.request.urlopen(req_new, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    if "embedding" in data:
        return _validate_embedding_dim(data["embedding"])
    embeddings = data.get("embeddings") or []
    if embeddings and isinstance(embeddings, list):
        first = embeddings[0]
        if isinstance(first, list):
            return _validate_embedding_dim(first)
    raise RuntimeError("Ollama embedding response did not contain embedding values")


def _validate_embedding_dim(values: list[float]) -> np.ndarray:
    vec = np.array(values, dtype="float32")
    if vec.ndim != 1:
        raise RuntimeError(f"Expected 1-D embedding vector, got shape={tuple(vec.shape)}")
    if vec.shape[0] != 768:
        raise RuntimeError(f"Expected 768-dim embedding vector, got {vec.shape[0]}")
    return vec


def search_semantic(
    conn,
    query: str,
    repo: str | None = None,
    limit: int = 10,
) -> list[dict]:
    """Cosine similarity search over stored embeddings."""
    from symdex.core.storage import query_symbols_with_embeddings

    query_vec = embed_text(query)
    rows = query_symbols_with_embeddings(conn, repo=repo)

    if not rows:
        return []

    results = []
    for row in rows:
        blob = row["embedding"]
        stored_vec = np.frombuffer(blob, dtype="float32")
        score = float(np.dot(query_vec, stored_vec))
        result = {k: v for k, v in row.items() if k != "embedding"}
        result["score"] = round(score, 4)
        results.append(result)

    results.sort(key=lambda x: x["score"], reverse=True)
    return results[:limit]
