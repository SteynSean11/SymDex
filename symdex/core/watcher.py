# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

"""Background file-system watcher that keeps the SymDex index up to date."""

import logging
import os
import threading
import time
from typing import Optional

from watchdog.events import FileSystemEventHandler, FileSystemEvent
from watchdog.observers import Observer

from symdex.core.indexer import index_folder, _SKIP_DIRS, _SKIP_EXTENSIONS
from symdex.core.storage import get_db_path, get_connection

logger = logging.getLogger(__name__)

_SKIP_DIR_PARTS = _SKIP_DIRS


def _should_skip(path: str) -> bool:
    """Return True if this path should never be indexed."""
    parts = path.replace("\\", "/").split("/")
    for part in parts[:-1]:  # directories in the path
        if part in _SKIP_DIR_PARTS:
            return True
    ext = os.path.splitext(path)[1].lower()
    return ext in _SKIP_EXTENSIONS


def _remove_file_from_index(repo: str, rel_path: str) -> None:
    """Delete all symbols and file hash record for a deleted file."""
    db_path = get_db_path(repo)
    conn = get_connection(db_path)
    try:
        conn.execute("DELETE FROM symbols WHERE repo=? AND file=?", (repo, rel_path))
        conn.execute("DELETE FROM files WHERE repo=? AND path=?", (repo, rel_path))
        conn.commit()
        logger.info("Removed deleted file from index: %s", rel_path)
    finally:
        conn.close()


class _Handler(FileSystemEventHandler):
    def __init__(self, root: str, repo: str) -> None:
        self._root = root
        self._repo = repo
        self._lock = threading.Lock()
        self._changed: set[str] = set()
        self._deleted: set[str] = set()

    def on_modified(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue(event.src_path)

    def on_created(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            self._queue(event.src_path)

    def on_deleted(self, event: FileSystemEvent) -> None:
        if not event.is_directory:
            rel = os.path.relpath(event.src_path, self._root).replace("\\", "/")
            with self._lock:
                self._deleted.add(rel)

    def _queue(self, abs_path: str) -> None:
        if _should_skip(abs_path):
            return
        with self._lock:
            self._changed.add(abs_path)

    def flush(self) -> tuple[set[str], set[str]]:
        with self._lock:
            changed, deleted = self._changed.copy(), self._deleted.copy()
            self._changed.clear()
            self._deleted.clear()
        return changed, deleted


def watch(
    path: str,
    name: Optional[str] = None,
    interval: float = 5.0,
    stop_event: Optional[threading.Event] = None,
) -> None:
    """Watch *path* and keep its SymDex index up to date.

    Performs an initial full index, then re-indexes changed files and
    removes deleted files every *interval* seconds.

    Args:
        path: Absolute or relative path to the directory to watch.
        name: Repo name. Defaults to folder basename.
        interval: Seconds between flush cycles.
        stop_event: Optional threading.Event to signal shutdown.
    """
    abs_path = os.path.abspath(path)
    repo = (name or os.path.basename(abs_path)).lower()

    logger.info("Initial index of %s ...", abs_path)
    index_folder(abs_path, repo)

    handler = _Handler(abs_path, repo)
    observer = Observer()
    observer.schedule(handler, abs_path, recursive=True)
    observer.start()
    logger.info("Watching %s (repo=%s, interval=%.1fs)", abs_path, repo, interval)

    try:
        while stop_event is None or not stop_event.is_set():
            time.sleep(interval)
            changed, deleted = handler.flush()

            for rel in deleted:
                _remove_file_from_index(repo, rel)

            if changed:
                logger.info("Re-indexing %d changed file(s) ...", len(changed))
                index_folder(abs_path, repo)

    finally:
        observer.stop()
        observer.join()
