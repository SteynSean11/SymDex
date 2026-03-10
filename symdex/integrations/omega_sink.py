from __future__ import annotations

import asyncio
import json
import logging
import os
import pathlib
import sqlite3
import urllib.request
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def _is_enabled() -> bool:
    return os.environ.get("SYMDEX_OMEGA_ENABLED", "0").lower() in {"1", "true", "yes", "on"}


def mirror_symbol(
    *,
    repo: str,
    file_path: str,
    name: str,
    kind: str,
    start_byte: int,
    end_byte: int,
    signature: str | None,
    embedding: list[float] | None,
) -> None:
    """Mirror symbol+embedding into Omega sinks (best-effort, non-fatal)."""
    if not _is_enabled():
        return

    if embedding is not None:
        _mirror_vector_http(
            content=_build_content(name=name, signature=signature),
            vector=embedding,
            source_file=file_path,
        )
    _mirror_graph_symbol(
        repo=repo,
        file_path=file_path,
        name=name,
        kind=kind,
        start_byte=start_byte,
        end_byte=end_byte,
        signature=signature,
    )


def mirror_call_edge(
    *,
    repo: str,
    file_path: str,
    caller_name: str,
    callee_name: str,
    callee_file: str | None,
) -> None:
    """Mirror call edge into the configured graph backend."""
    if not _is_enabled():
        return
    _mirror_graph_edge(
        repo=repo,
        file_path=file_path,
        caller_name=caller_name,
        callee_name=callee_name,
        callee_file=callee_file,
    )


def _build_content(*, name: str, signature: str | None) -> str:
    sig = signature or ""
    return f"{name}\n{sig}".strip()


def _mirror_vector_http(*, content: str, vector: list[float], source_file: str) -> None:
    omega_url = os.environ.get("SYMDEX_OMEGA_HTTP_URL", "http://127.0.0.1:8000").rstrip("/")
    table_name = os.environ.get("SYMDEX_OMEGA_VECTOR_TABLE", "symdex_symbols")
    model = os.environ.get("SYMDEX_OLLAMA_MODEL", "qwen3-embedding:0.6b")
    timeout = float(os.environ.get("SYMDEX_OMEGA_HTTP_TIMEOUT", "5"))

    if len(vector) != 768:
        logger.warning("omega_vector_mirror_skipped_non_768_dim: dims=%s", len(vector))
        return

    payload = {
        "content": content,
        "vector": [float(v) for v in vector],
        "source_file": source_file,
        "model": model,
        "table_name": table_name,
    }
    req = urllib.request.Request(
        url=f"{omega_url}/v1/ingest/deep",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=timeout):
            return
    except Exception as exc:  # noqa: BLE001
        logger.warning("omega_vector_mirror_failed: %s", exc)


def _graph_backend() -> str:
    return os.environ.get("SYMDEX_OMEGA_GRAPH_BACKEND", "sqlite").lower()


def _mirror_graph_symbol(
    *,
    repo: str,
    file_path: str,
    name: str,
    kind: str,
    start_byte: int,
    end_byte: int,
    signature: str | None,
) -> None:
    backend = _graph_backend()
    if backend == "sqlite":
        _upsert_symbol_sqlite(
            repo=repo,
            file_path=file_path,
            name=name,
            kind=kind,
            start_byte=start_byte,
            end_byte=end_byte,
            signature=signature,
        )
        return
    if backend == "aiosqlite":
        _run_async(_upsert_symbol_aiosqlite(
            repo=repo,
            file_path=file_path,
            name=name,
            kind=kind,
            start_byte=start_byte,
            end_byte=end_byte,
            signature=signature,
        ))
        return
    logger.warning("Unsupported SYMDEX_OMEGA_GRAPH_BACKEND=%s; expected sqlite|aiosqlite", backend)


def _mirror_graph_edge(
    *,
    repo: str,
    file_path: str,
    caller_name: str,
    callee_name: str,
    callee_file: str | None,
) -> None:
    backend = _graph_backend()
    if backend == "sqlite":
        _insert_edge_sqlite(
            repo=repo,
            file_path=file_path,
            caller_name=caller_name,
            callee_name=callee_name,
            callee_file=callee_file,
        )
        return
    if backend == "aiosqlite":
        _run_async(_insert_edge_aiosqlite(
            repo=repo,
            file_path=file_path,
            caller_name=caller_name,
            callee_name=callee_name,
            callee_file=callee_file,
        ))
        return
    logger.warning("Unsupported SYMDEX_OMEGA_GRAPH_BACKEND=%s; expected sqlite|aiosqlite", backend)


def _graph_db_path() -> pathlib.Path:
    raw = os.environ.get("SYMDEX_OMEGA_GRAPH_PATH", "~/.omega/symdex/symbols_calls.db")
    p = pathlib.Path(os.path.expanduser(raw))
    p.parent.mkdir(parents=True, exist_ok=True)
    return p


def _run_async(coro) -> None:
    try:
        asyncio.run(coro)
    except Exception as exc:  # noqa: BLE001
        logger.warning("omega_graph_mirror_failed: %s", exc)


async def _ensure_graph_schema_aiosqlite(conn) -> None:
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symdex_symbols (
            repo TEXT NOT NULL,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_byte INTEGER NOT NULL,
            end_byte INTEGER NOT NULL,
            signature TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (repo, file_path, name, start_byte, end_byte)
        )
        """
    )
    await conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symdex_edges (
            repo TEXT NOT NULL,
            file_path TEXT NOT NULL,
            caller_name TEXT NOT NULL,
            callee_name TEXT NOT NULL,
            callee_file TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    await conn.commit()


def _ensure_graph_schema_sqlite(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symdex_symbols (
            repo TEXT NOT NULL,
            file_path TEXT NOT NULL,
            name TEXT NOT NULL,
            kind TEXT NOT NULL,
            start_byte INTEGER NOT NULL,
            end_byte INTEGER NOT NULL,
            signature TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (repo, file_path, name, start_byte, end_byte)
        )
        """
    )
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS symdex_edges (
            repo TEXT NOT NULL,
            file_path TEXT NOT NULL,
            caller_name TEXT NOT NULL,
            callee_name TEXT NOT NULL,
            callee_file TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def _upsert_symbol_sqlite(
    *,
    repo: str,
    file_path: str,
    name: str,
    kind: str,
    start_byte: int,
    end_byte: int,
    signature: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db_path = _graph_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_graph_schema_sqlite(conn)
        conn.execute(
            """
            INSERT OR REPLACE INTO symdex_symbols
            (repo, file_path, name, kind, start_byte, end_byte, signature, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo, file_path, name, kind, start_byte, end_byte, signature, now),
        )
        conn.commit()
    finally:
        conn.close()


def _insert_edge_sqlite(
    *,
    repo: str,
    file_path: str,
    caller_name: str,
    callee_name: str,
    callee_file: str | None,
) -> None:
    now = datetime.now(timezone.utc).isoformat()
    db_path = _graph_db_path()
    conn = sqlite3.connect(db_path)
    try:
        _ensure_graph_schema_sqlite(conn)
        conn.execute(
            """
            INSERT INTO symdex_edges
            (repo, file_path, caller_name, callee_name, callee_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (repo, file_path, caller_name, callee_name, callee_file, now),
        )
        conn.commit()
    finally:
        conn.close()


async def _upsert_symbol_aiosqlite(
    *,
    repo: str,
    file_path: str,
    name: str,
    kind: str,
    start_byte: int,
    end_byte: int,
    signature: str | None,
) -> None:
    import aiosqlite

    now = datetime.now(timezone.utc).isoformat()
    db_path = _graph_db_path()
    async with aiosqlite.connect(db_path) as conn:
        await _ensure_graph_schema_aiosqlite(conn)
        await conn.execute(
            """
            INSERT OR REPLACE INTO symdex_symbols
            (repo, file_path, name, kind, start_byte, end_byte, signature, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (repo, file_path, name, kind, start_byte, end_byte, signature, now),
        )
        await conn.commit()


async def _insert_edge_aiosqlite(
    *,
    repo: str,
    file_path: str,
    caller_name: str,
    callee_name: str,
    callee_file: str | None,
) -> None:
    import aiosqlite

    now = datetime.now(timezone.utc).isoformat()
    db_path = _graph_db_path()
    async with aiosqlite.connect(db_path) as conn:
        await _ensure_graph_schema_aiosqlite(conn)
        await conn.execute(
            """
            INSERT INTO symdex_edges
            (repo, file_path, caller_name, callee_name, callee_file, updated_at)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (repo, file_path, caller_name, callee_name, callee_file, now),
        )
        await conn.commit()
