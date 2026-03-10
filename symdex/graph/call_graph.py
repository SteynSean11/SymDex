# symdex/graph/call_graph.py
# Copyright (c) 2026 Muhammad Husnain
# License: See LICENSE file in the project root.

from __future__ import annotations
import importlib
import logging
import os
import sqlite3

from symdex.integrations.omega_sink import mirror_call_edge

logger = logging.getLogger(__name__)

_EXT_MAP: dict[str, tuple[str, str]] = {
    ".py":   ("python",     "tree_sitter_python"),
    ".js":   ("javascript", "tree_sitter_javascript"),
    ".mjs":  ("javascript", "tree_sitter_javascript"),
    ".ts":   ("typescript", "tree_sitter_typescript"),
    ".tsx":  ("typescript", "tree_sitter_typescript"),
    ".go":   ("go",         "tree_sitter_go"),
    ".rs":   ("rust",       "tree_sitter_rust"),
    ".java": ("java",       "tree_sitter_java"),
}


def _get_language(ext: str):
    entry = _EXT_MAP.get(ext.lower())
    if not entry:
        return None, None
    lang_name, module_name = entry
    try:
        from tree_sitter import Language, Parser as TSParser  # noqa: F401
        mod = importlib.import_module(module_name)
        language = Language(mod.language())
        return lang_name, language
    except Exception as exc:
        logger.warning("Could not load grammar for %s: %s", ext, exc)
        return None, None


def _find_calls_in_range(node, start_byte: int, end_byte: int) -> list[str]:
    """Return callee names for all call nodes within [start_byte, end_byte)."""
    results = []
    if node.end_byte <= start_byte or node.start_byte >= end_byte:
        return results
    if node.type == "call" and start_byte <= node.start_byte < end_byte:
        func_node = node.child_by_field_name("function")
        if func_node:
            if func_node.type == "attribute":
                attr = func_node.child_by_field_name("attribute")
                name = attr.text.decode("utf-8", "replace") if attr else func_node.text.decode("utf-8", "replace")
            else:
                name = func_node.text.decode("utf-8", "replace")
            if name:
                results.append(name)
    for child in node.children:
        results.extend(_find_calls_in_range(child, start_byte, end_byte))
    return results


def extract_edges(
    conn: sqlite3.Connection,
    repo: str,
    file_path: str,
    abs_file: str,
    symbols: list[dict],
) -> None:
    """Extract call edges from a file and store them in the edges table."""
    if not symbols:
        return
    ext = os.path.splitext(abs_file)[1]
    _, language = _get_language(ext)
    if language is None:
        return

    try:
        source_bytes = open(abs_file, "rb").read()
    except OSError as exc:
        logger.warning("Could not read %s for edge extraction: %s", abs_file, exc)
        return

    try:
        from tree_sitter import Parser as TSParser
        parser = TSParser(language)
        tree = parser.parse(source_bytes)
    except Exception as exc:
        logger.warning("Tree-sitter parse failed for %s: %s", abs_file, exc)
        return

    # Delete old edges for this file's symbols
    conn.execute(
        "DELETE FROM edges WHERE caller_id IN (SELECT id FROM symbols WHERE repo=? AND file=?)",
        (repo, file_path),
    )

    for sym in symbols:
        sym_id = sym.get("id")
        if sym_id is None:
            continue
        start_b = sym.get("start_byte", 0)
        end_b = sym.get("end_byte", 0)
        callee_names = _find_calls_in_range(tree.root_node, start_b, end_b)
        for callee_name in callee_names:
            # Attempt to resolve file
            row = conn.execute(
                "SELECT file FROM symbols WHERE repo=? AND name=? LIMIT 1",
                (repo, callee_name),
            ).fetchone()
            callee_file = row["file"] if row else None
            conn.execute(
                "INSERT OR IGNORE INTO edges (caller_id, callee_name, callee_file) VALUES (?, ?, ?)",
                (sym_id, callee_name, callee_file),
            )
            mirror_call_edge(
                repo=repo,
                file_path=file_path,
                caller_name=sym.get("name", ""),
                callee_name=callee_name,
                callee_file=callee_file,
            )

    conn.commit()


def get_callers(conn: sqlite3.Connection, name: str, repo: str) -> list[dict]:
    """Return symbols that call the function named `name` in `repo`."""
    rows = conn.execute("""
        SELECT s.id, s.repo, s.file, s.name, s.kind, s.start_byte, s.end_byte, s.signature
        FROM edges e
        JOIN symbols s ON e.caller_id = s.id
        WHERE e.callee_name = ? AND s.repo = ?
    """, (name, repo)).fetchall()
    return [dict(r) for r in rows]


def get_callees(conn: sqlite3.Connection, name: str, repo: str) -> list[dict]:
    """Return names called by the function named `name` in `repo`."""
    rows = conn.execute("""
        SELECT e.callee_name, e.callee_file
        FROM edges e
        JOIN symbols s ON e.caller_id = s.id
        WHERE s.name = ? AND s.repo = ?
    """, (name, repo)).fetchall()
    return [{"name": r["callee_name"], "file": r["callee_file"]} for r in rows]
