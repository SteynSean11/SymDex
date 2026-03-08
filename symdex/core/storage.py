# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

import fnmatch
import os
import sqlite3
import pathlib
from typing import Optional

import numpy as np
import sqlite_vec

DEFAULT_SYMBOL_LIMIT = 20


def get_connection(db_path: str) -> sqlite3.Connection:
    """Open or create a SQLite DB, apply schema, enable WAL mode."""
    parent = os.path.dirname(db_path)
    if parent:
        os.makedirs(parent, exist_ok=True)
    conn = sqlite3.connect(db_path, check_same_thread=False)
    conn.enable_load_extension(True)
    sqlite_vec.load(conn)
    conn.enable_load_extension(False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    schema_path = pathlib.Path(__file__).parent / "schema.sql"
    conn.executescript(schema_path.read_text())
    conn.commit()
    return conn


def upsert_symbol(
    conn: sqlite3.Connection,
    repo: str,
    file: str,
    name: str,
    kind: str,
    start_byte: int,
    end_byte: int,
    signature: Optional[str],
    docstring: Optional[str],
) -> int:
    """Insert or replace a symbol. Returns the row id."""
    with conn:
        conn.execute(
            "DELETE FROM symbols WHERE repo=? AND file=? AND name=?",
            (repo, file, name),
        )
        cursor = conn.execute(
            """
        INSERT INTO symbols (repo, file, name, kind, start_byte, end_byte, signature, docstring)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
            (repo, file, name, kind, start_byte, end_byte, signature, docstring),
        )
    return cursor.lastrowid


def upsert_file(conn: sqlite3.Connection, repo: str, path: str, file_hash: str) -> None:
    """Insert or replace a file hash record."""
    conn.execute(
        "INSERT OR REPLACE INTO files (repo, path, hash) VALUES (?, ?, ?)",
        (repo, path, file_hash),
    )
    conn.commit()


def get_file_hash(conn: sqlite3.Connection, repo: str, path: str) -> Optional[str]:
    """Return stored SHA256 hash for (repo, path), or None if not indexed."""
    row = conn.execute(
        "SELECT hash FROM files WHERE repo=? AND path=?", (repo, path)
    ).fetchone()
    return row["hash"] if row else None


def query_symbols(
    conn: sqlite3.Connection,
    repo: Optional[str],
    name_pattern: str,
    kind: Optional[str] = None,
    limit: int = DEFAULT_SYMBOL_LIMIT,
) -> list[dict]:
    """Prefix search, falling back to contains search. Returns list of dicts."""
    kind_clause = " AND kind=?" if kind else ""
    repo_clause = " AND repo=?" if repo else ""

    def _run(pattern: str) -> list:
        sql = (
            "SELECT name, file, kind, start_byte, end_byte, signature, docstring "
            "FROM symbols WHERE name LIKE ?" + repo_clause + kind_clause + " LIMIT ?"
        )
        args: list = [pattern]
        if repo:
            args.append(repo)
        if kind:
            args.append(kind)
        args.append(limit)
        return conn.execute(sql, args).fetchall()

    rows = _run(f"{name_pattern}%")
    if not rows:
        rows = _run(f"%{name_pattern}%")
    return [dict(r) for r in rows]


def query_file_symbols(
    conn: sqlite3.Connection, repo: str, file: str
) -> list[dict]:
    """Return all symbols in a specific file, ordered by byte offset."""
    rows = conn.execute(
        "SELECT name, file, kind, start_byte, end_byte, signature, docstring "
        "FROM symbols WHERE repo=? AND file=? ORDER BY start_byte",
        (repo, file),
    ).fetchall()
    return [dict(r) for r in rows]


def search_text_in_index(
    conn: sqlite3.Connection,
    repo: str,
    query: str,
    repo_root: str,
    file_pattern: Optional[str] = None,
) -> list[dict]:
    """Scan indexed files on disk for lines matching query (case-insensitive).

    Returns [{file, line, text}]. Max 5 matches per file, 100 total.
    """
    rows = conn.execute(
        "SELECT DISTINCT path FROM files WHERE repo=?", (repo,)
    ).fetchall()

    results = []
    query_lower = query.lower()

    for row in rows:
        rel_path = row["path"]
        if file_pattern and not fnmatch.fnmatch(rel_path, file_pattern):
            continue

        abs_path = os.path.join(repo_root, rel_path)
        try:
            with open(abs_path, "r", encoding="utf-8", errors="ignore") as fh:
                file_matches = 0
                for line_num, line in enumerate(fh, start=1):
                    if query_lower in line.lower():
                        results.append({"file": rel_path, "line": line_num, "text": line.rstrip()})
                        file_matches += 1
                        if file_matches >= 5:
                            break
        except OSError:
            continue

        if len(results) >= 100:
            break

    return results


def get_db_path(repo_name: str) -> str:
    """Return path to ~/.symdex/<repo_name>.db — repo_name is normalized to lowercase."""
    base = os.path.join(os.path.expanduser("~"), ".symdex")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, f"{repo_name.lower()}.db")


def get_registry_path() -> str:
    """Return path to the central registry: ~/.symdex/registry.db"""
    base = os.path.join(os.path.expanduser("~"), ".symdex")
    os.makedirs(base, exist_ok=True)
    return os.path.join(base, "registry.db")


def _get_registry_connection() -> sqlite3.Connection:
    """Open the central registry DB, create repos table if needed."""
    conn = sqlite3.connect(get_registry_path())
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repos (
            name         TEXT PRIMARY KEY,
            root_path    TEXT NOT NULL,
            db_path      TEXT NOT NULL,
            last_indexed DATETIME
        )
        """
    )
    conn.commit()
    return conn


def upsert_repo(name: str, root_path: str, db_path: str) -> None:
    """Register or update a repo in the central registry."""
    name = name.lower()
    conn = _get_registry_connection()
    try:
        conn.execute(
            """
            INSERT INTO repos (name, root_path, db_path, last_indexed)
            VALUES (?, ?, ?, datetime('now'))
            ON CONFLICT(name) DO UPDATE SET
                root_path    = excluded.root_path,
                db_path      = excluded.db_path,
                last_indexed = excluded.last_indexed
            """,
            (name, root_path, db_path),
        )
        conn.commit()
    finally:
        conn.close()


def query_repos() -> list[dict]:
    """Return all registered repos from the central registry, ordered by name."""
    conn = _get_registry_connection()
    try:
        rows = conn.execute(
            "SELECT name, root_path, db_path, last_indexed FROM repos ORDER BY name"
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def upsert_embedding(conn: sqlite3.Connection, symbol_id: int, embedding: np.ndarray) -> None:
    """Store float32 embedding blob for a symbol."""
    blob = embedding.astype("float32").tobytes()
    conn.execute("UPDATE symbols SET embedding = ? WHERE id = ?", (blob, symbol_id))
    conn.commit()


def query_symbols_with_embeddings(
    conn: sqlite3.Connection, repo: str | None = None
) -> list[dict]:
    """Return all symbols that have a non-NULL embedding."""
    sql = (
        "SELECT id, repo, file, name, kind, start_byte, end_byte, "
        "signature, docstring, embedding FROM symbols WHERE embedding IS NOT NULL"
    )
    params: list = []
    if repo:
        sql += " AND repo = ?"
        params.append(repo)
    rows = conn.execute(sql, params).fetchall()
    return [dict(r) for r in rows]
