"""Ontology MCP: the queries of an MCP scope as tools for external agents.

Following Palantir's Ontology MCP, nothing is open by default: a client sends
the token of one scope (``Authorization: Bearer omcp_...``), sees only the
queries that scope lists, and every call runs as the scope's owner, so results
are the intersection of the scope and that user's permissions.  Served over
streamable HTTP by the official MCP Python SDK, mounted in the API at /api/v2/mcp/.
"""
# No `from __future__ import annotations`: FastMCP reads the real annotations to find the Context parameter.
import hashlib
from contextlib import contextmanager

import anyio
from mcp.server.fastmcp import Context, FastMCP

from app.database import SessionLocal
from app.models.user import User
from app.models.v2.ontology_query import McpScope, OntologyQuery
from app.services.v2 import ontology_queries
from app.services.v2.object_query.authorization import require_ontology_access

MAX_ROWS = 200   # agents get a readable answer, not a data dump

mcp = FastMCP("ontology-queries", stateless_http=True, json_response=True, streamable_http_path="/")


class Denied(Exception):
    pass


@contextmanager
def _scope(ctx: Context):
    """The scope named by the request's bearer token, its owner and a DB session."""
    request = ctx.request_context.request
    header = request.headers.get("authorization", "") if request is not None else ""
    token = header.removeprefix("Bearer ").strip()
    if not token:
        raise Denied("Missing bearer token: send the token of an MCP scope")
    db = SessionLocal()
    try:
        scope = db.query(McpScope).filter_by(token_hash=hashlib.sha256(token.encode()).hexdigest()).first()
        if scope is None or scope.revoked_at is not None:
            raise Denied("Unknown or revoked MCP scope token")
        user = db.get(User, scope.owner_id)
        if user is None or not user.is_active:
            raise Denied("The scope's owner is no longer active")
        yield db, scope, user
    finally:
        db.close()


def _allowed(db, scope: McpScope, api_name: str):
    entry = next((row for row in scope.queries if row["api_name"] == api_name), None)
    if entry is None:
        raise Denied(f"{api_name} is not open in this scope")
    query = db.query(OntologyQuery).filter_by(ontology_id=scope.ontology_id, api_name=api_name).first()
    if query is None:
        raise Denied(f"{api_name} no longer exists")
    return query, ontology_queries.pick(db, query, entry["range"])


def _describe(query, version) -> dict:
    return {"api_name": query.api_name, "name": query.name, "description": query.description, "version": version.version,
            "params": [{"name": p["name"], "label": p.get("label") or p["field"], "required": p.get("required", False)} for p in version.definition.get("params", [])],
            "time_range": "all | 1d | 7d | 30d, counted back from the latest time in the data", "output": version.output_schema}


@mcp.tool()
async def list_queries(ctx: Context) -> list[dict]:
    """List the ontology queries this scope may run, with their parameters and output columns."""
    def work():
        with _scope(ctx) as (db, scope, _user):
            return [_describe(*_allowed(db, scope, row["api_name"])) for row in scope.queries]
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def describe_query(api_name: str, ctx: Context) -> dict:
    """Describe one query: what it returns (shape, columns with roles and labels) and which parameters it takes."""
    def work():
        with _scope(ctx) as (db, scope, _user):
            return _describe(*_allowed(db, scope, api_name))
    return await anyio.to_thread.run_sync(work)


@mcp.tool()
async def run_query(api_name: str, ctx: Context, params: dict | None = None, time_range: str = "all", limit: int = 50) -> dict:
    """Run a query.  params: equality parameters by name; time_range: all, 1d, 7d or 30d; at most `limit` rows (max 200) are returned."""
    def work():
        with _scope(ctx) as (db, scope, user):
            query, version = _allowed(db, scope, api_name)
            access = require_ontology_access(db, scope.ontology_id, str(user.id), user.role)
            result = ontology_queries.run(db, scope.ontology_id, version, params or {}, time_range if time_range in ("all", "1d", "7d", "30d") else "all",
                                          user, access, limit=min(max(int(limit), 1), MAX_ROWS))
            rows = result["outputs"].get(version.output_schema["path"]) or []
            return {"api_name": api_name, "version": version.version, "time": result["outputs"].get("time"), "row_count": len(rows),
                    "rows": rows[:min(max(int(limit), 1), MAX_ROWS)], "columns": version.output_schema["columns"], "shape": version.output_schema["shape"]}
    return await anyio.to_thread.run_sync(work)


_current_app = None


def start():
    """A fresh session manager for each API lifespan (one can only run once); returns it for the lifespan to run."""
    global _current_app
    mcp._session_manager = None
    _current_app = mcp.streamable_http_app()
    return mcp.session_manager


async def http_app(scope, receive, send):
    """Mounted at /api/v2/mcp; forwards to the app of the current lifespan."""
    if _current_app is None:
        raise RuntimeError("Ontology MCP is not running (API lifespan not started)")
    await _current_app(scope, receive, send)
