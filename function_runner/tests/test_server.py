"""Linux subprocess checks; container-only network/filesystem probes live in backend/tests."""
import importlib.util
from pathlib import Path
import sys

import pytest

spec = importlib.util.spec_from_file_location("runner_server", Path(__file__).parents[1] / "server.py")
server = importlib.util.module_from_spec(spec)
spec.loader.exec_module(server)

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="runner uses Linux resource limits and pipes")


def test_success_after_a_crash():
    assert server.run("raise ValueError('bad code')", {}, 2, 256)["error"]["code"] == "function_load_failed"
    assert server.run("def run(inputs): return inputs", {"value": 7}, 2, 256) == {"output": {"value": 7}}


@pytest.mark.parametrize("stream", ["stdout", "stderr"])
def test_stream_flood_does_not_take_down_subsequent_runs(stream, monkeypatch):
    monkeypatch.setattr(server, "MAX_OUTPUT_BYTES", 4096)
    monkeypatch.setattr(server, "MAX_STDERR_BYTES", 4096)
    source = f"import sys\ndef run(inputs):\n while True:\n  sys.{stream}.write('x' * 8192)\n  sys.{stream}.flush()"
    assert server.run(source, {}, 2, 256)["error"]["code"] == "function_output_too_large"
    assert server.run("def run(inputs): return 42", {}, 2, 256) == {"output": 42}


def test_timeout_is_reaped_and_next_run_succeeds():
    assert server.run("def run(inputs):\n while True: pass", {}, 1, 256)["error"]["code"] == "function_timeout"
    assert server.run("def run(inputs): return 'ok'", {}, 2, 256) == {"output": "ok"}
