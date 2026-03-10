# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

import sqlite3
import tempfile
import os
import pytest
from symdex.core.storage import (
    get_connection,
    get_db_path,
    get_file_hash,
    get_stale_repos,
    query_file_symbols,
    query_symbols,
    remove_repo,
    upsert_file,
    upsert_repo,
    upsert_symbol,
)


@pytest.fixture
def tmp_db(tmp_path):
    db_file = str(tmp_path / "test.db")
    conn = get_connection(db_file)
    yield conn
    conn.close()


def test_get_connection_creates_all_tables(tmp_path):
    db_file = str(tmp_path / "test.db")
    conn = get_connection(db_file)
    tables = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    ).fetchall()
    table_names = {t[0] for t in tables}
    assert {"symbols", "edges", "files", "repos"} <= table_names
    conn.close()


def test_upsert_symbol_and_query_back(tmp_db):
    sym_id = upsert_symbol(
        tmp_db,
        repo="myrepo",
        file="src/foo.py",
        name="my_func",
        kind="function",
        start_byte=0,
        end_byte=100,
        signature="def my_func():",
        docstring="Does the thing.",
    )
    assert isinstance(sym_id, int)
    results = query_symbols(tmp_db, repo="myrepo", name_pattern="my_func")
    assert len(results) == 1
    r = results[0]
    assert r["name"] == "my_func"
    assert r["kind"] == "function"
    assert r["file"] == "src/foo.py"
    assert r["start_byte"] == 0
    assert r["end_byte"] == 100
    assert r["signature"] == "def my_func():"
    assert r["docstring"] == "Does the thing."


def test_upsert_symbol_no_duplicate_on_reinsert(tmp_db):
    upsert_symbol(tmp_db, repo="r", file="f.py", name="fn", kind="function",
                  start_byte=0, end_byte=50, signature="def fn():", docstring=None)
    upsert_symbol(tmp_db, repo="r", file="f.py", name="fn", kind="function",
                  start_byte=0, end_byte=60, signature="def fn():", docstring=None)
    results = query_symbols(tmp_db, repo="r", name_pattern="fn")
    assert len(results) == 1
    assert results[0]["end_byte"] == 60


def test_upsert_file_and_retrieve_hash(tmp_db):
    upsert_file(tmp_db, repo="myrepo", path="src/foo.py", file_hash="abc123")
    retrieved = get_file_hash(tmp_db, repo="myrepo", path="src/foo.py")
    assert retrieved == "abc123"


def test_upsert_file_replaces_old_hash(tmp_db):
    upsert_file(tmp_db, repo="myrepo", path="src/foo.py", file_hash="old_hash")
    upsert_file(tmp_db, repo="myrepo", path="src/foo.py", file_hash="new_hash")
    retrieved = get_file_hash(tmp_db, repo="myrepo", path="src/foo.py")
    assert retrieved == "new_hash"


def test_get_file_hash_returns_none_for_unknown(tmp_db):
    result = get_file_hash(tmp_db, repo="myrepo", path="does_not_exist.py")
    assert result is None


def test_query_file_symbols(tmp_db):
    upsert_symbol(tmp_db, repo="r", file="a.py", name="A", kind="class",
                  start_byte=0, end_byte=10, signature="class A:", docstring=None)
    upsert_symbol(tmp_db, repo="r", file="b.py", name="B", kind="function",
                  start_byte=0, end_byte=10, signature="def B():", docstring=None)
    results = query_file_symbols(tmp_db, repo="r", file="a.py")
    assert len(results) == 1
    assert results[0]["name"] == "A"


def test_get_db_path_returns_symdex_dir():
    path = get_db_path("myrepo")
    assert path.endswith("myrepo.db")
    assert ".symdex" in path


def test_search_text_in_index_finds_matches(tmp_path):
    """search_text_in_index scans indexed files on disk and returns matching lines."""
    from symdex.core.storage import search_text_in_index

    # Write a file to the tmp directory
    (tmp_path / "hello.py").write_text("def hello():\n    print('hello world')\n")

    # Get a DB and index the file
    db_file = str(tmp_path / "test.db")
    conn = get_connection(db_file)
    upsert_file(conn, repo="r", path="hello.py", file_hash="fakehash")

    results = search_text_in_index(conn, repo="r", query="hello world", repo_root=str(tmp_path))
    assert len(results) >= 1
    assert any("hello world" in m["text"] for m in results)
    assert all("file" in m and "line" in m and "text" in m for m in results)


def test_search_text_in_index_respects_file_pattern(tmp_path):
    """file_pattern glob filters which files are searched."""
    from symdex.core.storage import search_text_in_index

    (tmp_path / "a.py").write_text("target_word here\n")
    (tmp_path / "b.txt").write_text("target_word here\n")

    db_file = str(tmp_path / "test.db")
    conn = get_connection(db_file)
    upsert_file(conn, repo="r", path="a.py", file_hash="h1")
    upsert_file(conn, repo="r", path="b.txt", file_hash="h2")

    results = search_text_in_index(conn, repo="r", query="target_word",
                                   repo_root=str(tmp_path), file_pattern="*.py")
    assert all(m["file"].endswith(".py") for m in results)


# --- get_stale_repos / remove_repo tests ---

@pytest.fixture
def patched_registry(tmp_path, monkeypatch):
    """Redirect registry to a tmp path."""
    registry_file = str(tmp_path / "registry.db")
    monkeypatch.setattr("symdex.core.storage.get_registry_path", lambda: registry_file)
    return tmp_path


def test_get_stale_repos_returns_missing_path(tmp_path, patched_registry):
    db_file = str(tmp_path / "dead.db")
    upsert_repo("dead-repo", root_path="/nonexistent/path/xyz", db_path=db_file)
    stale = get_stale_repos()
    names = [r["name"] for r in stale]
    assert "dead-repo" in names


def test_get_stale_repos_excludes_live_repo(tmp_path, patched_registry):
    live_dir = tmp_path / "live"
    live_dir.mkdir()
    db_file = str(tmp_path / "live.db")
    upsert_repo("live-repo", root_path=str(live_dir), db_path=db_file)
    stale = get_stale_repos()
    names = [r["name"] for r in stale]
    assert "live-repo" not in names


def test_remove_repo_deletes_db_and_registry_entry(tmp_path, patched_registry):
    db_file = str(tmp_path / "todelete.db")
    # Create a real db file to verify it gets removed
    open(db_file, "w").close()
    upsert_repo("todelete", root_path="/nonexistent", db_path=db_file)
    remove_repo("todelete")
    assert not os.path.isfile(db_file)
    stale = get_stale_repos()
    assert all(r["name"] != "todelete" for r in stale)
