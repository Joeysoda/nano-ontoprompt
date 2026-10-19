"""Isolation acceptance for the user-Function runner container.

Runs only against a real runner (FUNCTION_RUNNER_URL).  Each probe is a user
Function trying to escape a limit; the runner must report a stable failure,
not a success.
"""
import os

import httpx
import pytest

URL = os.environ.get("FUNCTION_RUNNER_URL", "")
pytestmark = pytest.mark.skipif(not URL, reason="needs a real function-runner container")


def run(source, inputs=None, **limits):
    response = httpx.post(f"{URL}/run", json={"source": source, "inputs": inputs or {}, **limits}, timeout=60)
    assert response.status_code == 200, response.text
    return response.json()


def test_function_runs_and_is_deterministic():
    source = "import numpy as np\ndef run(inputs):\n    return {'total': float(np.sum(inputs['values'])), 'keys': sorted({'b':1,'a':2})}"
    first = run(source, {"values": [1, 2, 3.5]})
    assert first == {"output": {"total": 6.5, "keys": ["a", "b"]}}
    assert run(source, {"values": [1, 2, 3.5]}) == first


def test_network_is_unreachable():
    source = ("import socket\ndef run(inputs):\n    s = socket.create_connection(('1.1.1.1', 443), timeout=3)\n    return {'connected': True}")
    result = run(source)
    assert "output" not in result and result["error"]["code"] == "function_raised"


def test_database_hosts_are_not_resolvable_from_the_runner():
    source = ("import socket\ndef run(inputs):\n    return {'ip': socket.gethostbyname(inputs['host'])}")
    for host in ("db", "redis", "falkordb"):
        assert run(source, {"host": host})["error"]["code"] == "function_raised", host


def test_environment_is_empty():
    source = "import os\ndef run(inputs):\n    return {'keys': sorted(os.environ)}"
    keys = set(run(source)["output"]["keys"])
    assert keys <= {"PATH", "PYTHONHASHSEED", "OPENBLAS_NUM_THREADS", "OMP_NUM_THREADS", "LC_CTYPE"}


def test_memory_limit():
    source = "def run(inputs):\n    block = bytearray(800 * 1024 * 1024)\n    return {'size': len(block)}"
    assert run(source, memory_mb=256)["error"]["code"] in {"function_memory_exceeded", "function_raised"}


def test_cpu_and_wall_time_limit():
    source = "def run(inputs):\n    while True:\n        pass"
    assert run(source, timeout_seconds=2)["error"]["code"] == "function_timeout"


def test_file_writes_and_process_spawning_fail():
    write = "def run(inputs):\n    open('escape.txt', 'w').write('x' * 10)\n    return {}"
    assert run(write)["error"]["code"] == "function_raised"
    spawn = "import subprocess\ndef run(inputs):\n    return {'out': subprocess.run(['id'], capture_output=True).stdout.decode()}"
    assert run(spawn)["error"]["code"] == "function_raised"


def test_bad_functions_report_stable_codes():
    assert run("x = 1")["error"]["code"] == "function_entrypoint_missing"
    assert run("def run(inputs):\n    return {'v': float('nan')}")["error"]["code"] == "function_output_not_json"
    assert run("def run(inputs:\n")["error"]["code"] == "function_load_failed"
