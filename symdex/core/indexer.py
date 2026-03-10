# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

import hashlib
import logging
import os
import re
import subprocess
from dataclasses import dataclass
from symdex.core.parser import parse_file
from symdex.graph.call_graph import extract_edges as _extract_edges
from symdex.core.route_extractor import extract_routes as _extract_routes
from symdex.integrations.omega_sink import mirror_symbol
from symdex.core.storage import (
    get_connection,
    get_db_path,
    get_file_hash,
    upsert_file,
    upsert_symbol,
    upsert_embedding,
    delete_file_routes,
    upsert_route,
)

logger = logging.getLogger(__name__)

def _embed_symbols(conn, repo: str, file_path: str) -> None:
    """Compute and store embeddings for all symbols in repo+file.

    Queries symbols already inserted for this repo/file, computes an embedding
    text from signature, docstring, and name, then calls embed_text and stores
    the result via upsert_embedding. Failures per symbol are logged and skipped
    so that a single bad symbol never aborts indexing.
    """
    from symdex.search.semantic import embed_text  # local import avoids circular dep

    rows = conn.execute(
        "SELECT id, name, kind, start_byte, end_byte, signature, docstring "
        "FROM symbols WHERE repo=? AND file=?",
        (repo, file_path),
    ).fetchall()

    for row in rows:
        symbol_id = row["id"]
        name = row["name"] or ""
        kind = row["kind"] or "symbol"
        start_byte = int(row["start_byte"] or 0)
        end_byte = int(row["end_byte"] or 0)
        signature = row["signature"] or ""
        docstring = row["docstring"] or ""
        embed_input = f"{signature}\n{docstring}\n{name}".strip()
        # Always mirror symbol metadata to Omega graph storage, even if embedding fails.
        mirror_symbol(
            repo=repo,
            file_path=file_path,
            name=name,
            kind=kind,
            start_byte=start_byte,
            end_byte=end_byte,
            signature=signature,
            embedding=None,
        )
        try:
            vec = embed_text(embed_input)
            upsert_embedding(conn, symbol_id, vec)
            mirror_symbol(
                repo=repo,
                file_path=file_path,
                name=name,
                kind=kind,
                start_byte=start_byte,
                end_byte=end_byte,
                signature=signature,
                embedding=vec.tolist(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("Embedding failed for symbol %s (id=%s): %s", name, symbol_id, exc)


_SKIP_DIRS = {
    ".git", "node_modules", "__pycache__", ".venv", "venv",
    ".tox", "dist", "build", ".mypy_cache", ".pytest_cache",
}

_SKIP_EXTENSIONS = {
    ".pyc", ".pyo", ".so", ".dylib", ".dll", ".exe", ".bin",
    ".png", ".jpg", ".jpeg", ".gif", ".ico", ".svg", ".pdf",
    ".zip", ".tar", ".gz", ".whl", ".egg",
    ".db", ".sqlite", ".sqlite3",
    ".lock",
}


@dataclass
class IndexResult:
    repo: str
    db_path: str
    indexed_count: int
    skipped_count: int


def get_git_branch(path: str) -> str | None:
    """Return the current git branch name for path, or None if not a git repo / detached HEAD."""
    try:
        result = subprocess.run(
            ["git", "-C", path, "symbolic-ref", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return None
        branch = result.stdout.strip()
        if not branch:
            return None
        # Sanitize: replace path separators and special chars with '-', lowercase
        sanitized = re.sub(r"[/\\@\s]+", "-", branch).lower().strip("-")
        return sanitized or None
    except Exception:  # noqa: BLE001
        return None


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def index_folder(path: str, name: str | None = None) -> IndexResult:
    """Index all source files in path. Skips unchanged files via SHA256 hash.

    Args:
        path: Absolute path to the directory to index.
        name: Repo name. Defaults to os.path.basename(path).

    Returns:
        IndexResult with repo, db_path, indexed_count, skipped_count.
    """
    abs_path = os.path.abspath(path)
    repo = (name or get_git_branch(abs_path) or os.path.basename(abs_path)).lower()
    db_path = get_db_path(repo)
    conn = get_connection(db_path)

    indexed = 0
    skipped = 0

    try:
        for dirpath, dirnames, filenames in os.walk(path):
            dirnames[:] = [d for d in dirnames if d not in _SKIP_DIRS]

            for filename in filenames:
                ext = os.path.splitext(filename)[1].lower()
                if ext in _SKIP_EXTENSIONS:
                    continue

                abs_file = os.path.join(dirpath, filename)
                rel_file = os.path.relpath(abs_file, path).replace("\\", "/")

                try:
                    current_hash = _sha256(abs_file)
                except OSError as exc:
                    logger.warning("Skipping %s: %s", abs_file, exc)
                    continue

                stored_hash = get_file_hash(conn, repo, rel_file)
                if stored_hash == current_hash:
                    skipped += 1
                    continue

                symbols = parse_file(abs_file, path)
                conn.execute(
                    "DELETE FROM symbols WHERE repo=? AND file=?", (repo, rel_file)
                )
                for sym in symbols:
                    upsert_symbol(
                        conn,
                        repo=repo,
                        file=rel_file,
                        name=sym["name"],
                        kind=sym["kind"],
                        start_byte=sym["start_byte"],
                        end_byte=sym["end_byte"],
                        signature=sym.get("signature"),
                        docstring=sym.get("docstring"),
                    )
                _embed_symbols(conn, repo=repo, file_path=rel_file)
                sym_rows = conn.execute(
                    "SELECT id, name, start_byte, end_byte FROM symbols WHERE repo=? AND file=?",
                    (repo, rel_file),
                ).fetchall()
                _extract_edges(conn, repo=repo, file_path=rel_file, abs_file=abs_file, symbols=[dict(r) for r in sym_rows])
                # Route extraction for Python and JS/TS files
                _ROUTE_LANG_MAP = {
                    ".py": "python", ".js": "javascript", ".ts": "typescript",
                    ".jsx": "javascript", ".tsx": "typescript",
                }
                file_lang = _ROUTE_LANG_MAP.get(ext)
                if file_lang:
                    try:
                        with open(abs_file, "rb") as rh:
                            raw = rh.read()
                        file_routes = _extract_routes(raw, rel_file, file_lang)
                        delete_file_routes(conn, repo=repo, file=rel_file)
                        for route in file_routes:
                            upsert_route(
                                conn,
                                repo=repo,
                                file=rel_file,
                                method=route.method,
                                path=route.path,
                                handler=route.handler,
                                start_byte=route.start_byte,
                                end_byte=route.end_byte,
                            )
                    except Exception as exc:  # noqa: BLE001
                        logger.warning("Route extraction failed for %s: %s", abs_file, exc)
                upsert_file(conn, repo=repo, path=rel_file, file_hash=current_hash)
                indexed += 1
        conn.commit()
    finally:
        conn.close()

    return IndexResult(repo=repo, db_path=db_path, indexed_count=indexed, skipped_count=skipped)


def invalidate(repo: str, file: str | None = None) -> int:
    """Delete hash records (and their symbols) for the repo or a specific file.

    Returns count of file records deleted.
    """
    db_path = get_db_path(repo)
    conn = get_connection(db_path)
    try:
        if file:
            cursor = conn.execute(
                "DELETE FROM files WHERE repo=? AND path=?", (repo, file)
            )
            conn.execute(
                "DELETE FROM symbols WHERE repo=? AND file=?", (repo, file)
            )
        else:
            cursor = conn.execute("DELETE FROM files WHERE repo=?", (repo,))
            conn.execute("DELETE FROM symbols WHERE repo=?", (repo,))
        count = cursor.rowcount
        conn.commit()
    finally:
        conn.close()
    return count
