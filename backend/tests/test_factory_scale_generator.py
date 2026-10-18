import json
from pathlib import Path
from uuid import uuid4

import pytest

from tests.data.generate_factory_scale import records, write_fixture


def test_scale_generator_is_repeatable_linked_and_streams_to_jsonl():
    first = list(records(12, seed=11))
    assert first == list(records(12, seed=11))
    assert first != list(records(12, seed=12))
    assert first[1]["links"][0]["target"] == first[0]["id"]
    assert first[2]["links"][0]["target"] == first[1]["id"]

    destination = Path(__file__).parent / f".factory-scale-smoke-{uuid4().hex}.jsonl"
    try:
        write_fixture(destination, count=10_000, seed=11)
        rows = destination.read_text(encoding="utf-8").splitlines()
        assert len(rows) == 10_000
        assert json.loads(rows[0])["type"] == "Supplier"
        assert json.loads(rows[2])["type"] == "WorkOrder"
    finally:
        destination.unlink(missing_ok=True)


def test_scale_generator_restricts_profiles_to_reviewed_sizes():
    with pytest.raises(ValueError, match="one of"):
        write_fixture(Path(__file__).parent / "unused.jsonl", count=999, seed=26)
