# Copyright (c) 2026 Muhammad Husnain
# This file is part of SymDex.
# License: See LICENSE file in the project root.

import pytest
from symdex.core.storage import get_connection


def test_routes_table_exists(tmp_path):
    db = str(tmp_path / "test.db")
    conn = get_connection(db)
    # If table doesn't exist this raises OperationalError
    conn.execute("SELECT * FROM routes LIMIT 0")
    conn.close()


from symdex.core.route_extractor import extract_routes, RouteInfo


FLASK_SOURCE = b'''
from flask import Flask
app = Flask(__name__)

@app.route("/users", methods=["GET", "POST"])
def list_users():
    pass

@app.get("/users/<int:id>")
def get_user(id):
    pass

@app.delete("/users/<int:id>")
def delete_user(id):
    pass
'''

FASTAPI_SOURCE = b'''
from fastapi import FastAPI
app = FastAPI()
router = APIRouter()

@app.get("/items")
async def list_items():
    pass

@router.post("/items")
async def create_item():
    pass
'''

DJANGO_SOURCE = b'''
from django.urls import path, re_path
from . import views

urlpatterns = [
    path("users/", views.list_users),
    path("users/<int:pk>/", views.get_user, name="user-detail"),
    re_path(r"^orders/(?P<id>[0-9]+)/$", views.get_order),
]
'''

EXPRESS_SOURCE = b'''
const express = require("express");
const router = express.Router();
const app = express();

app.get("/products", listProducts);
router.post("/products", createProduct);
app.delete("/products/:id", deleteProduct);
'''


def test_flask_route_detected():
    routes = extract_routes(FLASK_SOURCE, "app.py", "python")
    paths = [r.path for r in routes]
    assert "/users" in paths


def test_flask_route_method():
    routes = extract_routes(FLASK_SOURCE, "app.py", "python")
    r = next(r for r in routes if r.path == "/users" and r.method == "GET")
    assert r.handler == "list_users"


def test_flask_shorthand_get():
    routes = extract_routes(FLASK_SOURCE, "app.py", "python")
    paths = [r.path for r in routes]
    assert "/users/<int:id>" in paths


def test_flask_shorthand_delete():
    routes = extract_routes(FLASK_SOURCE, "app.py", "python")
    methods = {r.method for r in routes if r.path == "/users/<int:id>"}
    assert "DELETE" in methods


def test_fastapi_router():
    routes = extract_routes(FASTAPI_SOURCE, "main.py", "python")
    paths = [r.path for r in routes]
    assert "/items" in paths


def test_fastapi_post():
    routes = extract_routes(FASTAPI_SOURCE, "main.py", "python")
    posts = [r for r in routes if r.method == "POST"]
    assert len(posts) >= 1


def test_django_path():
    routes = extract_routes(DJANGO_SOURCE, "urls.py", "python")
    paths = [r.path for r in routes]
    assert "users/" in paths


def test_express_get():
    routes = extract_routes(EXPRESS_SOURCE, "routes.js", "javascript")
    methods = {r.method for r in routes if r.path == "/products"}
    assert "GET" in methods


def test_express_post():
    routes = extract_routes(EXPRESS_SOURCE, "routes.js", "javascript")
    posts = [r for r in routes if r.method == "POST"]
    assert len(posts) == 1


def test_express_delete():
    routes = extract_routes(EXPRESS_SOURCE, "routes.js", "javascript")
    deletes = [r for r in routes if r.method == "DELETE"]
    assert len(deletes) == 1


def test_route_info_has_bytes():
    routes = extract_routes(FLASK_SOURCE, "app.py", "python")
    for r in routes:
        assert r.start_byte >= 0
        assert r.end_byte > r.start_byte


def test_empty_source_returns_empty():
    assert extract_routes(b"", "empty.py", "python") == []


def test_unsupported_lang_returns_empty():
    assert extract_routes(b"some content", "file.rs", "rust") == []


from symdex.core.storage import upsert_route, query_routes, delete_file_routes


def test_upsert_and_query_route(tmp_path):
    db = str(tmp_path / "r.db")
    conn = get_connection(db)
    upsert_route(conn, repo="myapp", file="api.py", method="GET",
                 path="/users", handler="list_users", start_byte=0, end_byte=100)
    conn.commit()
    rows = query_routes(conn, repo="myapp")
    assert len(rows) == 1
    assert rows[0]["path"] == "/users"
    assert rows[0]["method"] == "GET"
    conn.close()


def test_query_routes_filter_method(tmp_path):
    db = str(tmp_path / "r2.db")
    conn = get_connection(db)
    upsert_route(conn, repo="r", file="f.py", method="GET",  path="/a", handler="h1", start_byte=0, end_byte=10)
    upsert_route(conn, repo="r", file="f.py", method="POST", path="/b", handler="h2", start_byte=11, end_byte=20)
    conn.commit()
    rows = query_routes(conn, repo="r", method="POST")
    assert len(rows) == 1
    assert rows[0]["path"] == "/b"
    conn.close()


def test_query_routes_filter_path(tmp_path):
    db = str(tmp_path / "r3.db")
    conn = get_connection(db)
    upsert_route(conn, repo="r", file="f.py", method="GET", path="/users", handler="h", start_byte=0, end_byte=10)
    upsert_route(conn, repo="r", file="f.py", method="GET", path="/items", handler="h", start_byte=11, end_byte=20)
    conn.commit()
    rows = query_routes(conn, repo="r", path_contains="user")
    assert len(rows) == 1
    conn.close()


def test_delete_file_routes(tmp_path):
    db = str(tmp_path / "r4.db")
    conn = get_connection(db)
    upsert_route(conn, repo="r", file="api.py", method="GET", path="/a", handler="h", start_byte=0, end_byte=10)
    upsert_route(conn, repo="r", file="other.py", method="GET", path="/b", handler="h", start_byte=0, end_byte=10)
    conn.commit()
    delete_file_routes(conn, repo="r", file="api.py")
    conn.commit()
    rows = query_routes(conn, repo="r")
    assert all(row["file"] == "other.py" for row in rows)
    conn.close()


def test_index_folder_extracts_routes(tmp_path):
    """index_folder should populate the routes table for Flask files."""
    from unittest.mock import patch

    repo_dir = tmp_path / "flask_app"
    repo_dir.mkdir()
    (repo_dir / "views.py").write_text(
        'from flask import Flask\napp = Flask(__name__)\n\n'
        '@app.get("/hello")\ndef hello(): pass\n'
    )

    from symdex.core.indexer import index_folder
    from symdex.core.storage import get_connection, query_routes

    db_path_store = {}

    def fake_db_path(repo):
        import os
        p = str(tmp_path / f"{repo}.db")
        db_path_store[repo] = p
        return p

    with patch("symdex.core.indexer.get_db_path", fake_db_path), \
         patch("symdex.core.storage.get_db_path", fake_db_path), \
         patch("symdex.search.semantic.embed_text", return_value=[0.0] * 384):
        result = index_folder(str(repo_dir), name="flask_test")

    conn = get_connection(db_path_store["flask_test"])
    routes = query_routes(conn, repo="flask_test")
    conn.close()
    assert any(r["path"] == "/hello" for r in routes)
