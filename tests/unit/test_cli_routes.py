# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

from typer.testing import CliRunner
from symdex.cli import app


def test_routes_help():
    runner = CliRunner()
    result = runner.invoke(app, ["routes", "--help"])
    assert result.exit_code == 0
    assert "REPO" in result.output or "repo" in result.output


def test_routes_command_no_routes(tmp_path, monkeypatch):
    monkeypatch.setattr("symdex.core.storage.get_db_path", lambda repo: str(tmp_path / f"{repo}.db"))
    monkeypatch.setattr("symdex.cli.get_db_path", lambda repo: str(tmp_path / f"{repo}.db"))
    runner = CliRunner()
    result = runner.invoke(app, ["routes", "empty_repo"])
    assert result.exit_code == 0
    assert "No routes" in result.output or "0" in result.output


def test_routes_command_shows_route(tmp_path, monkeypatch):
    from symdex.core.storage import get_connection, upsert_route
    db_path = str(tmp_path / "myapp.db")
    monkeypatch.setattr("symdex.core.storage.get_db_path", lambda repo: db_path)
    monkeypatch.setattr("symdex.cli.get_db_path", lambda repo: db_path)

    conn = get_connection(db_path)
    upsert_route(conn, repo="myapp", file="api.py", method="GET",
                 path="/users", handler="list_users", start_byte=0, end_byte=100)
    conn.commit()
    conn.close()

    runner = CliRunner()
    result = runner.invoke(app, ["routes", "myapp"])
    assert result.exit_code == 0
    assert "/users" in result.output
