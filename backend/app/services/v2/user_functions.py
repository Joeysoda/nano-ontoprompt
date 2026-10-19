"""User-authored Functions: validated source, pinned digest, isolated execution.

Source is stored inside the immutable Logic Asset release (``config.source``)
together with its SHA-256.  Execution always goes to the function-runner
container (``FUNCTION_RUNNER_URL``); if it is unavailable the call fails with
a stable code.  User code is never executed in the backend process.
"""
from __future__ import annotations

import ast
import os
from hashlib import sha256

import httpx

IMPLEMENTATION = "user_python"
MAX_SOURCE_BYTES = 256 * 1024
RUNTIME = "python3.12+numpy2.1.3+scipy1.14.1"
DEFAULT_LIMITS = {"timeout_seconds": 30, "memory_mb": 512}


class UserFunctionError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 422):
        super().__init__(message)
        self.code, self.message, self.status = code, message, status


def source_digest(source: str) -> str:
    return sha256(source.encode("utf-8")).hexdigest()


def validate_source(source: str) -> None:
    if not isinstance(source, str) or not source.strip():
        raise UserFunctionError("invalid_source", "Function source is empty")
    if len(source.encode("utf-8")) > MAX_SOURCE_BYTES:
        raise UserFunctionError("invalid_source", "Function source exceeds 256 KB")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        raise UserFunctionError("invalid_source", f"Syntax error on line {exc.lineno}: {exc.msg}") from exc
    entry = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == "run"]
    if len(entry) != 1 or len(entry[0].args.args) != 1:
        raise UserFunctionError("invalid_source", "Source must define exactly one top-level run(inputs)")


def release_config(source: str, limits: dict | None = None, extra: dict | None = None) -> dict:
    validate_source(source)
    limits = {**DEFAULT_LIMITS, **(limits or {})}
    if not (1 <= int(limits["timeout_seconds"]) <= 120 and 64 <= int(limits["memory_mb"]) <= 2048):
        raise UserFunctionError("invalid_limits", "timeout 1-120 s and memory 64-2048 MB")
    return {**(extra or {}), "source": source, "source_digest": source_digest(source), "runtime": RUNTIME,
            "limits": {"timeout_seconds": int(limits["timeout_seconds"]), "memory_mb": int(limits["memory_mb"])}}


def run(config: dict, inputs: dict) -> dict:
    source = config.get("source") or ""
    if source_digest(source) != config.get("source_digest"):
        raise UserFunctionError("source_digest_mismatch", "Stored Function source does not match its release digest", 409)
    url = os.environ.get("FUNCTION_RUNNER_URL", "")
    if not url:
        raise UserFunctionError("function_runner_unavailable", "No function runner is configured", 503)
    limits = config.get("limits") or DEFAULT_LIMITS
    headers = {"Authorization": f"Bearer {os.environ['FUNCTION_RUNNER_TOKEN']}"} if os.environ.get("FUNCTION_RUNNER_TOKEN") else {}
    try:
        response = httpx.post(f"{url.rstrip('/')}/run", json={"source": source, "inputs": inputs, **limits},
                              headers=headers, timeout=limits["timeout_seconds"] + 15)
    except httpx.HTTPError as exc:
        raise UserFunctionError("function_runner_unavailable", f"Function runner unreachable: {type(exc).__name__}", 503) from exc
    if response.status_code != 200:
        raise UserFunctionError("function_runner_unavailable", f"Function runner returned {response.status_code}", 503)
    body = response.json()
    if "error" in body:
        raise UserFunctionError(body["error"]["code"], body["error"]["message"])
    if not isinstance(body.get("output"), dict):
        raise UserFunctionError("function_output_not_object", "run(inputs) must return a JSON object")
    return body["output"]
