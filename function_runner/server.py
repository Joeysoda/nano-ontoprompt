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
import selectors
import signal
import subprocess
import sys
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

WORKER = str(Path(__file__).with_name("worker.py"))
MAX_REQUEST_BYTES = 20 * 1024 * 1024
MAX_OUTPUT_BYTES = 20 * 1024 * 1024
MAX_STDERR_BYTES = 64 * 1024
MAX_TIMEOUT_SECONDS = 120
MAX_MEMORY_MB = 2048
TOKEN = os.environ.get("FUNCTION_RUNNER_TOKEN", "")
SLOTS = threading.BoundedSemaphore(int(os.environ.get("FUNCTION_RUNNER_CONCURRENCY", "4")))


class OutputLimitExceeded(Exception):
    pass


def _collect(process, payload: bytes, timeout: int) -> tuple[bytes, bytes]:
    """Bound both streams while reading; never buffer unlimited child output."""
    stdout, stderr = bytearray(), bytearray()
    pending = memoryview(payload)
    deadline = time.monotonic() + timeout
    with selectors.DefaultSelector() as selector:
        for stream, event in ((process.stdin, selectors.EVENT_WRITE),
                              (process.stdout, selectors.EVENT_READ),
                              (process.stderr, selectors.EVENT_READ)):
            os.set_blocking(stream.fileno(), False)
            selector.register(stream, event)
        while selector.get_map():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise subprocess.TimeoutExpired(process.args, timeout)
            for key, _ in selector.select(min(remaining, 0.1)):
                stream = key.fileobj
                if stream is process.stdin:
                    try:
                        written = os.write(stream.fileno(), pending[:65536])
                        pending = pending[written:]
                    except BrokenPipeError:
                        pending = pending[:0]
                    if not pending:
                        selector.unregister(stream)
                        stream.close()
                    continue
                chunk = os.read(stream.fileno(), 65536)
                if not chunk:
                    selector.unregister(stream)
                    continue
                target, cap = (stdout, MAX_OUTPUT_BYTES) if stream is process.stdout else (stderr, MAX_STDERR_BYTES)
                if len(target) + len(chunk) > cap:
                    raise OutputLimitExceeded()
                target.extend(chunk)
        process.wait(timeout=max(0.01, deadline - time.monotonic()))
    return bytes(stdout), bytes(stderr)


def run(source: str, inputs, timeout_seconds: int, memory_mb: int) -> dict:
    process = None
    try:
        process = subprocess.Popen(
            [sys.executable, "-I", WORKER, str(timeout_seconds), str(memory_mb)],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd="/",
            env={"PATH": "/usr/bin:/bin", "PYTHONHASHSEED": "0", "OPENBLAS_NUM_THREADS": "1", "OMP_NUM_THREADS": "1"},
            start_new_session=True, close_fds=True,
        )
        stdout, stderr = _collect(process, json.dumps({"source": source, "inputs": inputs}).encode(), timeout_seconds + 2)
    except OutputLimitExceeded:
        return {"error": {"code": "function_output_too_large", "message": "Function stdout or stderr exceeded its size limit"}}
    except subprocess.TimeoutExpired:
        return {"error": {"code": "function_timeout", "message": f"Function exceeded {timeout_seconds}s"}}
    finally:
        if process is not None:
            if process.poll() is None:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            process.wait()
            for stream in (process.stdin, process.stdout, process.stderr):
                stream.close()
    if process.returncode in (-24, -9) or process.returncode == 152:  # SIGXCPU / SIGKILL after the CPU limit
        return {"error": {"code": "function_timeout", "message": f"Function exceeded {timeout_seconds}s of CPU"}}
    try:
        result = json.loads(stdout or b"{}")
        if isinstance(result, dict) and (("output" in result) != ("error" in result)):
            return result
    except json.JSONDecodeError:
        pass
    return {"error": {"code": "function_crashed", "message": stderr.decode(errors="replace")[-2000:]}}


class Handler(BaseHTTPRequestHandler):
    def setup(self):
        super().setup()
        self.connection.settimeout(10)

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
        try:
            length = int(self.headers.get("Content-Length") or 0)
        except ValueError:
            return self._send(400, {"error": {"code": "invalid_request", "message": "Invalid Content-Length"}})
        if length <= 0 or length > MAX_REQUEST_BYTES:
            return self._send(413, {"error": {"code": "request_too_large", "message": "Request size is outside the limit"}})
        try:
            body = json.loads(self.rfile.read(length))
            source, inputs = body["source"], body.get("inputs") or {}
            timeout_seconds = min(int(body.get("timeout_seconds", 30)), MAX_TIMEOUT_SECONDS)
            memory_mb = min(int(body.get("memory_mb", 512)), MAX_MEMORY_MB)
            if not isinstance(source, str) or timeout_seconds < 1 or memory_mb < 64:
                raise ValueError("Invalid source or resource limits")
        except (KeyError, TypeError, ValueError):
            return self._send(400, {"error": {"code": "invalid_request", "message": "Expected source, inputs and limits"}})
        if not SLOTS.acquire(blocking=False):
            return self._send(503, {"error": {"code": "function_runner_busy", "message": "All execution slots are busy"}})
        try:
            return self._send(200, run(source, inputs, timeout_seconds, memory_mb))
        finally:
            SLOTS.release()

    def log_message(self, format, *args):  # request logs carry no code or inputs
        sys.stderr.write(f"{self.address_string()} {format % args}\n")


class BoundedServer(ThreadingHTTPServer):
    """Bound request threads as well as executing Functions."""
    daemon_threads = True
    request_queue_size = 8

    def __init__(self, *args, **kwargs):
        self.requests = threading.BoundedSemaphore(8)
        super().__init__(*args, **kwargs)

    def process_request(self, request, client_address):
        if not self.requests.acquire(blocking=False):
            self.shutdown_request(request)
            return
        try:
            super().process_request(request, client_address)
        except Exception:
            self.requests.release()
            raise

    def process_request_thread(self, request, client_address):
        try:
            super().process_request_thread(request, client_address)
        finally:
            self.requests.release()


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("FUNCTION_RUNNER_TOKEN is required")
    port = int(os.environ.get("PORT", "8100"))
    BoundedServer(("0.0.0.0", port), Handler).serve_forever()
