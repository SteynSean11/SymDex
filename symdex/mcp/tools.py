# symdex/mcp/tools.py
# Copyright (c) 2026 Muhammad Husnain
# License: See LICENSE file in the project root.

import os

from symdex.core.indexer import index_folder as _index_folder, invalidate
from symdex.core.storage import (
    get_connection,
    get_db_path,
    get_registry_path,  # noqa: F401 — imported so tests can monkeypatch this module's reference
    query_file_symbols,
    query_repos,
    upsert_repo,
    search_text_in_index,
)
from symdex.search.symbol_search import search_symbols as _search_symbols


def _err(code: int, key: str, message: str) -> dict:
    return {"error": {"code": code, "key": key, "message": message}}


def _get_root_path(repo: str) -> str | None:
    """Look up repo root_path from central registry. Returns None if not registered."""
    for r in query_repos():
        if r["name"] == repo:
            return r["root_path"]
    return None


def _build_tree(root: str, prefix: str = "", depth: int = 3, current_depth: int = 0) -> str:
    """Render a text-art directory tree, depth-limited."""
    if current_depth >= depth:
        return ""
    try:
        entries = sorted(os.listdir(root))
    except OSError:
        return ""
    lines = []
    for i, entry in enumerate(entries):
        connector = "└── " if i == len(entries) - 1 else "├── "
        lines.append(f"{prefix}{connector}{entry}")
        full_path = os.path.join(root, entry)
        if os.path.isdir(full_path):
            extension = "    " if i == len(entries) - 1 else "│   "
            subtree = _build_tree(full_path, prefix + extension, depth, current_depth + 1)
            if subtree:
                lines.append(subtree)
    return "\n".join(lines)


def index_folder_tool(path: str, name: str | None = None) -> dict:
    if not os.path.isdir(path):
        return _err(400, "invalid_request", f"Path does not exist or is not a directory: {path}")
    result = _index_folder(path, name=name)
    upsert_repo(result.repo, root_path=os.path.abspath(path), db_path=result.db_path)
    return {
        "repo": result.repo,
        "db_path": result.db_path,
        "indexed": result.indexed_count,
        "skipped": result.skipped_count,
    }


def search_symbols_tool(
    query: str,
    repo: str | None = None,
    kind: str | None = None,
    limit: int = 20,
) -> dict:
    if not query:
        return _err(400, "invalid_request", "query must be a non-empty string")
    if repo is None:
        from symdex.graph.registry import search_across_repos
        symbols = search_across_repos(query=query, kind=kind, limit=limit)
        if not symbols:
            return _err(404, "symbol_not_found", f"No symbols matching: {query}")
        return {"symbols": symbols}
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        symbols = _search_symbols(conn, repo=repo, query=query, kind=kind, limit=limit)
    finally:
        conn.close()
    if not symbols:
        return _err(404, "symbol_not_found", f"No symbols matching: {query}")
    return {"symbols": symbols}


def get_symbol_tool(repo: str, file: str, start_byte: int, end_byte: int) -> dict:
    if end_byte <= start_byte:
        return _err(400, "invalid_request", "end_byte must be greater than start_byte")
    root = _get_root_path(repo)
    if root is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    abs_path = os.path.join(root, file)
    if not os.path.isfile(abs_path):
        return _err(404, "file_not_found", f"File not found: {file}")
    conn = get_connection(get_db_path(repo))
    try:
        row = conn.execute(
            "SELECT name, kind FROM symbols WHERE repo=? AND file=? AND start_byte=? AND end_byte=?",
            (repo, file, start_byte, end_byte),
        ).fetchone()
    finally:
        conn.close()
    with open(abs_path, "rb") as fh:
        fh.seek(start_byte)
        source = fh.read(end_byte - start_byte).decode("utf-8", errors="replace")
    name = dict(row)["name"] if row else ""
    kind = dict(row)["kind"] if row else ""
    return {"name": name, "kind": kind, "source": source}


def get_file_outline_tool(repo: str, file: str) -> dict:
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        symbols = query_file_symbols(conn, repo, file)
    finally:
        conn.close()
    if not symbols:
        return _err(404, "file_not_found", f"No symbols indexed for file: {file}")
    return {"file": file, "symbols": symbols}


def get_repo_outline_tool(repo: str) -> dict:
    root = _get_root_path(repo)
    if root is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        file_count = conn.execute(
            "SELECT COUNT(DISTINCT path) FROM files WHERE repo=?", (repo,)
        ).fetchone()[0]
        symbol_count = conn.execute(
            "SELECT COUNT(*) FROM symbols WHERE repo=?", (repo,)
        ).fetchone()[0]
    finally:
        conn.close()
    tree = _build_tree(root, depth=3)
    return {
        "repo": repo,
        "tree": tree,
        "stats": {"files": file_count, "symbols": symbol_count},
    }


def search_text_tool(
    query: str,
    repo: str | None = None,
    file_pattern: str | None = None,
) -> dict:
    if not query:
        return _err(400, "invalid_request", "query must be a non-empty string")
    if repo is None:
        return _err(400, "invalid_request", "repo is required (cross-repo search available in Phase 6)")
    root = _get_root_path(repo)
    if root is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        matches = search_text_in_index(
            conn, repo=repo, query=query, repo_root=root, file_pattern=file_pattern
        )
    finally:
        conn.close()
    return {"matches": matches}


def get_file_tree_tool(repo: str, depth: int = 3) -> dict:
    root = _get_root_path(repo)
    if root is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    return {"tree": _build_tree(root, depth=depth)}


def list_repos_tool() -> dict:
    repos = query_repos()
    return {
        "repos": [
            {
                "name": r["name"],
                "root_path": r["root_path"],
                "last_indexed": r["last_indexed"],
            }
            for r in repos
        ]
    }


def get_symbols_tool(names: list[str], repo: str | None = None) -> dict:
    if repo is None:
        return _err(400, "invalid_request", "repo is required (cross-repo search available in Phase 6)")
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        all_symbols: list[dict] = []
        for name in names:
            results = _search_symbols(conn, repo=repo, query=name, limit=10)
            all_symbols.extend(s for s in results if s["name"] == name)
    finally:
        conn.close()
    return {"symbols": all_symbols}


def index_repo_tool(name: str, path: str) -> dict:
    if not os.path.isdir(path):
        return _err(400, "invalid_request", f"Path does not exist or is not a directory: {path}")
    result = _index_folder(path, name=name)
    upsert_repo(name, root_path=os.path.abspath(path), db_path=result.db_path)
    return {
        "repo": result.repo,
        "db_path": result.db_path,
        "indexed": result.indexed_count,
        "skipped": result.skipped_count,
    }


def invalidate_cache_tool(repo: str, file: str | None = None) -> dict:
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    count = invalidate(repo, file=file)
    return {"invalidated": count}


def semantic_search_tool(query: str, repo: str | None = None, limit: int = 10) -> dict:
    """Find symbols by meaning using embedding similarity."""
    from symdex.search.semantic import search_semantic

    if not repo:
        return _err(400, "invalid_request", "repo parameter is required")
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        results = search_semantic(conn, query=query, repo=repo, limit=limit)
    except Exception as exc:
        return _err(500, "embedding_error", str(exc))
    finally:
        conn.close()
    return {"symbols": results}


def get_callers_tool(name: str, repo: str) -> dict:
    """Return all symbols that call the named function."""
    from symdex.graph.call_graph import get_callers
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        sym = conn.execute("SELECT id FROM symbols WHERE name=? AND repo=?", (name, repo)).fetchone()
        if not sym:
            return _err(404, "symbol_not_found", f"Symbol not found: {name}")
        callers = get_callers(conn, name=name, repo=repo)
    finally:
        conn.close()
    return {"callers": callers}


def get_callees_tool(name: str, repo: str) -> dict:
    """Return all functions called by the named function."""
    from symdex.graph.call_graph import get_callees
    if _get_root_path(repo) is None:
        return _err(404, "repo_not_indexed", f"Repo not indexed: {repo}")
    conn = get_connection(get_db_path(repo))
    try:
        sym = conn.execute("SELECT id FROM symbols WHERE name=? AND repo=?", (name, repo)).fetchone()
        if not sym:
            return _err(404, "symbol_not_found", f"Symbol not found: {name}")
        callees = get_callees(conn, name=name, repo=repo)
    finally:
        conn.close()
    return {"callees": callees}


def search_routes_tool(
    repo: str,
    method: str | None = None,
    path_contains: str | None = None,
    limit: int = 50,
) -> dict:
    """Find HTTP routes indexed from a repo.

    Args:
        repo: Repo name as registered with index_folder or index_repo.
        method: Filter by HTTP method (GET, POST, PUT, DELETE, PATCH). Optional.
        path_contains: Filter routes whose path contains this substring. Optional.
        limit: Maximum results. Default 50.

    Returns:
        {"routes": [{method, path, handler, file, start_byte, end_byte}, ...]}
    """
    from symdex.core.storage import query_routes
    db_path = get_db_path(repo)
    conn = get_connection(db_path)
    try:
        rows = query_routes(conn, repo=repo, method=method,
                            path_contains=path_contains, limit=limit)
    finally:
        conn.close()
    return {"routes": rows}
