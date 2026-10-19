"""Isolated executor for user-authored Functions.

POST /run {"source", "inputs", "timeout_seconds", "memory_mb"} runs the
source's run(inputs) in a new process with CPU, address-space, file-size and
process-count limits and an empty environment.  The container has no outbound
network (it sits only on an internal Docker network), runs as an unprivileged
user, and its filesystem is read-only with no writable mount at all, so one
run cannot leave anything behind for the next.

Every failure is reported with a stable code; the caller never falls back to
running user code in-process.
"""
from __future__ import annotations

import json
import os
import resource
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKER = str(Path(__file__).with_name("worker.py"))
MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_TIMEOUT_SECONDS = 120
MAX_MEMORY_MB = 2048
TOKEN = os.environ.get("FUNCTION_RUNNER_TOKEN", "")
SLOTS = threading.BoundedSemaphore(int(os.environ.get("FUNCTION_RUNNER_CONCURRENCY", "4")))


def _limits(timeout_seconds: int, memory_mb: int):
    def apply():
        os.setsid()
        resource.setrlimit(resource.RLIMIT_CPU, (timeout_seconds, timeout_seconds + 1))
        resource.setrlimit(resource.RLIMIT_AS, (memory_mb * 1024 * 1024,) * 2)
        resource.setrlimit(resource.RLIMIT_FSIZE, (0, 0))      # no file writes
        resource.setrlimit(resource.RLIMIT_NPROC, (0, 0))      # no forks or threads beyond the main one
        resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    return apply


def run(source: str, inputs, timeout_seconds: int, memory_mb: int) -> dict:
    try:
        done = subprocess.run(
            [sys.executable, "-I", WORKER], input=json.dumps({"source": source, "inputs": inputs}).encode(),
            capture_output=True, timeout=timeout_seconds + 2, cwd="/",
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            preexec_fn=_limits(timeout_seconds, memory_mb), close_fds=True,
        )
    except subprocess.TimeoutExpired:
        return {"error": {"code": "function_timeout", "message": f"Function exceeded {timeout_seconds}s"}}
    if done.returncode in (-24, -9) or done.returncode == 152:  # SIGXCPU / SIGKILL after the CPU limit
        return {"error": {"code": "function_timeout", "message": f"Function exceeded {timeout_seconds}s of CPU"}}
    if len(done.stdout) > MAX_OUTPUT_BYTES:
        return {"error": {"code": "function_output_too_large", "message": "Function output exceeded the size limit"}}
    try:
        return json.loads(done.stdout or b"{}") or {"error": {"code": "function_crashed", "message": done.stderr.decode(errors="replace")[-2000:]}}
    except json.JSONDecodeError:
        return {"error": {"code": "function_crashed", "message": done.stderr.decode(errors="replace")[-2000:]}}


class Handler(BaseHTTPRequestHandler):
    def _send(self, status: int, body: dict):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        if self.path == "/health":
            return self._send(200, {"status": "ok"})
        return self._send(404, {"error": {"code": "not_found", "message": self.path}})

    def do_POST(self):
        if self.path != "/run":
            return self._send(404, {"error": {"code": "not_found", "message": self.path}})
        if TOKEN and self.headers.get("Authorization") != f"Bearer {TOKEN}":
            return self._send(401, {"error": {"code": "unauthorized", "message": "Invalid runner token"}})
        length = int(self.headers.get("Content-Length") or 0)
        if length <= 0 or length > MAX_REQUEST_BYTES:
            return self._send(413, {"error": {"code": "request_too_large", "message": "Request size is outside the limit"}})
        try:
            body = json.loads(self.rfile.read(length))
            source, inputs = body["source"], body.get("inputs") or {}
            timeout_seconds = min(int(body.get("timeout_seconds", 30)), MAX_TIMEOUT_SECONDS)
            memory_mb = min(int(body.get("memory_mb", 512)), MAX_MEMORY_MB)
        except (KeyError, TypeError, ValueError):
            return self._send(400, {"error": {"code": "invalid_request", "message": "Expected source, inputs and limits"}})
        if not SLOTS.acquire(timeout=30):
            return self._send(503, {"error": {"code": "function_runner_busy", "message": "All execution slots are busy"}})
        try:
            return self._send(200, run(source, inputs, timeout_seconds, memory_mb))
        finally:
            SLOTS.release()

    def log_message(self, format, *args):  # request logs carry no code or inputs
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


if __name__ == "__main__":
    port = int(os.environ.get("PORT", "8100"))
    ThreadingHTTPServer(("0.0.0.0", port), Handler).serve_forever()
