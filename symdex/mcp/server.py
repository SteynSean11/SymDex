# symdex/mcp/server.py
# Copyright (c) 2026 Muhammad Husnain
# License: See LICENSE file in the project root.

from fastmcp import FastMCP

from symdex.mcp.tools import (
    index_folder_tool,
    search_symbols_tool,
    get_symbol_tool,
    get_file_outline_tool,
    get_repo_outline_tool,
    search_text_tool,
    get_file_tree_tool,
    list_repos_tool,
    get_symbols_tool,
    index_repo_tool,
    invalidate_cache_tool,
    gc_stale_indexes_tool,
    search_routes_tool,
)

mcp = FastMCP("symdex-mcp")


@mcp.tool(name="index_folder", description="Index a local folder and return indexing statistics.")
def index_folder(path: str, name: str | None = None) -> dict:
    return index_folder_tool(path=path, name=name)


@mcp.tool(name="search_symbols", description="Find functions/classes by name. ~200 tokens per lookup.")
def search_symbols(
    query: str, repo: str | None = None, kind: str | None = None, limit: int = 20
) -> dict:
    return search_symbols_tool(query=query, repo=repo, kind=kind, limit=limit)


@mcp.tool(name="get_symbol", description="Get full source of a symbol by byte offsets.")
def get_symbol(repo: str, file: str, start_byte: int, end_byte: int) -> dict:
    return get_symbol_tool(repo=repo, file=file, start_byte=start_byte, end_byte=end_byte)


@mcp.tool(name="get_file_outline", description="All symbols in a file without reading full content.")
def get_file_outline(repo: str, file: str) -> dict:
    return get_file_outline_tool(repo=repo, file=file)


@mcp.tool(name="get_repo_outline", description="Directory tree and symbol stats for an indexed repo.")
def get_repo_outline(repo: str) -> dict:
    return get_repo_outline_tool(repo=repo)


@mcp.tool(name="search_text", description="Text search across indexed files. Returns matching lines only.")
def search_text(query: str, repo: str | None = None, file_pattern: str | None = None) -> dict:
    return search_text_tool(query=query, repo=repo, file_pattern=file_pattern)


@mcp.tool(name="get_file_tree", description="Directory tree of an indexed repo without file contents.")
def get_file_tree(repo: str, depth: int = 3) -> dict:
    return get_file_tree_tool(repo=repo, depth=depth)


@mcp.tool(name="list_repos", description="List all indexed repositories in the central registry.")
def list_repos() -> dict:
    return list_repos_tool()


@mcp.tool(name="get_symbols", description="Bulk symbol retrieval by exact name list.")
def get_symbols(names: list[str], repo: str | None = None) -> dict:
    return get_symbols_tool(names=names, repo=repo)


@mcp.tool(name="index_repo", description="Index a named repo and register it in the central registry.")
def index_repo(name: str, path: str) -> dict:
    return index_repo_tool(name=name, path=path)


@mcp.tool(name="invalidate_cache", description="Force re-index of a repo or specific file on next call.")
def invalidate_cache(repo: str, file: str | None = None) -> dict:
    return invalidate_cache_tool(repo=repo, file=file)


@mcp.tool(name="search_routes", description="Find HTTP routes indexed from a repo. Filter by method or path substring.")
def search_routes(
    repo: str,
    method: str | None = None,
    path_contains: str | None = None,
    limit: int = 50,
) -> dict:
    return search_routes_tool(repo=repo, method=method, path_contains=path_contains, limit=limit)


@mcp.tool(name="gc_stale_indexes", description="Remove stale index databases for repos whose directories no longer exist on disk.")
def gc_stale_indexes() -> dict:
    return gc_stale_indexes_tool()


if __name__ == "__main__":
    mcp.run()
