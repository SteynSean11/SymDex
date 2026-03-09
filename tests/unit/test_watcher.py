# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

import os
import time
import threading
import pytest
from unittest.mock import patch

from symdex.core.watcher import _should_skip, watch

FAKE_VEC = [0.1] * 384


def test_should_skip_git_dir():
    assert _should_skip(".git/config") is True


def test_should_skip_pycache():
    assert _should_skip("__pycache__/mod.pyc") is True


def test_should_skip_binary():
    assert _should_skip("image.png") is True


def test_should_not_skip_py():
    assert _should_skip("src/main.py") is False


def test_should_not_skip_js():
    assert _should_skip("app/index.js") is False


def _make_db_path_factory(tmp_path):
    """Return a get_db_path function that stores DBs under tmp_path."""
    def _mock_get_db_path(repo_name: str) -> str:
        db_dir = str(tmp_path / ".symdex")
        os.makedirs(db_dir, exist_ok=True)
        return os.path.join(db_dir, f"{repo_name}.db")
    return _mock_get_db_path


def test_watch_reindexes_new_file(tmp_path):
    """watch() should pick up a new file written after start."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("def hello(): pass\n")

    mock_db_path = _make_db_path_factory(tmp_path)
    stop = threading.Event()

    def run():
        with patch("symdex.search.semantic.embed_text", return_value=FAKE_VEC), \
             patch("symdex.core.indexer.get_db_path", side_effect=mock_db_path), \
             patch("symdex.core.storage.get_db_path", side_effect=mock_db_path), \
             patch("symdex.core.watcher.get_db_path", side_effect=mock_db_path):
            watch(str(repo_dir), name="test_watch", interval=0.5, stop_event=stop)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)  # let first index run

    (repo_dir / "new_file.py").write_text("def new_func(): pass\n")
    time.sleep(1.5)  # let watcher pick it up

    stop.set()
    t.join(timeout=5)

    from symdex.core.storage import get_connection
    db_path = mock_db_path("test_watch")
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT name FROM symbols WHERE repo='test_watch' AND name='new_func'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1


def test_watch_removes_deleted_file(tmp_path):
    """watch() should remove symbols for deleted files from the index."""
    repo_dir = tmp_path / "repo"
    repo_dir.mkdir()
    (repo_dir / "main.py").write_text("def hello(): pass\n")
    to_delete = repo_dir / "old.py"
    to_delete.write_text("def goodbye(): pass\n")

    mock_db_path = _make_db_path_factory(tmp_path)
    stop = threading.Event()

    def run():
        with patch("symdex.search.semantic.embed_text", return_value=FAKE_VEC), \
             patch("symdex.core.indexer.get_db_path", side_effect=mock_db_path), \
             patch("symdex.core.storage.get_db_path", side_effect=mock_db_path), \
             patch("symdex.core.watcher.get_db_path", side_effect=mock_db_path):
            watch(str(repo_dir), name="test_watch_del", interval=0.5, stop_event=stop)

    t = threading.Thread(target=run, daemon=True)
    t.start()
    time.sleep(1.5)

    to_delete.unlink()
    time.sleep(1.5)

    stop.set()
    t.join(timeout=5)

    from symdex.core.storage import get_connection
    db_path = mock_db_path("test_watch_del")
    conn = get_connection(db_path)
    rows = conn.execute(
        "SELECT name FROM symbols WHERE repo='test_watch_del' AND name='goodbye'"
    ).fetchall()
    conn.close()
    assert len(rows) == 0
