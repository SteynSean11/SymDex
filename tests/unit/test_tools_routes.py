# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

import pytest
from unittest.mock import patch


def test_search_routes_tool_returns_routes(tmp_path, monkeypatch):
    from symdex.core.storage import get_connection, upsert_route

    monkeypatch.setattr("symdex.mcp.tools.get_db_path", lambda repo: str(tmp_path / f"{repo}.db"))
    monkeypatch.setattr("symdex.core.storage.get_db_path", lambda repo: str(tmp_path / f"{repo}.db"))

    db_path = str(tmp_path / "myapp.db")
    conn = get_connection(db_path)
    upsert_route(conn, repo="myapp", file="api.py", method="GET",
                 path="/users", handler="list_users", start_byte=0, end_byte=100)
    conn.commit()
    conn.close()

    from symdex.mcp.tools import search_routes_tool
    result = search_routes_tool(repo="myapp", method=None, path_contains=None)
    assert "routes" in result
    assert len(result["routes"]) == 1
    assert result["routes"][0]["path"] == "/users"


def test_search_routes_tool_empty(tmp_path, monkeypatch):
    monkeypatch.setattr("symdex.mcp.tools.get_db_path", lambda repo: str(tmp_path / f"{repo}.db"))
    monkeypatch.setattr("symdex.core.storage.get_db_path", lambda repo: str(tmp_path / f"{repo}.db"))

    from symdex.mcp.tools import search_routes_tool
    result = search_routes_tool(repo="empty_repo", method=None, path_contains=None)
    assert result["routes"] == []
