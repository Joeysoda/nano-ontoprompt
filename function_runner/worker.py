"""Runs one user function. Started by server.py as a fresh, resource-limited process.

Reads {"source", "inputs"} as JSON on stdin and writes {"output"} or
{"error": {"code", "message"}} as JSON on stdout.  Isolation (no network,
resource limits, unprivileged user, empty environment) is enforced by the
container and the parent process, not by anything in this file.
"""
import json
import sys
import traceback


def _fail(code, message):
    sys.stdout.write(json.dumps({"error": {"code": code, "message": message[:2000]}}))
    sys.stdout.flush()
    sys.exit(0)


def main():
    try:
        payload = json.loads(sys.stdin.read())
    except Exception:
        _fail("invalid_request", "Request is not valid JSON")
    namespace = {"__name__": "user_function"}
    try:
        exec(compile(payload["source"], "<user_function>", "exec"), namespace)
    except MemoryError:
        _fail("function_memory_exceeded", "Function exceeded its memory limit while loading")
    except Exception:
        _fail("function_load_failed", traceback.format_exc(limit=5))
    run = namespace.get("run")
    if not callable(run):
        _fail("function_entrypoint_missing", "Source must define run(inputs)")
    try:
        output = run(payload.get("inputs") or {})
    except MemoryError:
        _fail("function_memory_exceeded", "Function exceeded its memory limit")
    except Exception:
        _fail("function_raised", traceback.format_exc(limit=8))
    try:
        encoded = json.dumps({"output": output}, allow_nan=False)
    except (TypeError, ValueError) as exc:
        _fail("function_output_not_json", str(exc))
    sys.stdout.write(encoded)
    sys.stdout.flush()


if __name__ == "__main__":
    main()
