"""Stream reproducible, synthetic manufacturing objects for bounded load tests.

This is a test fixture generator, not customer or industrial evidence. It writes
JSONL so the 1m-object profile does not need to be held in memory.
"""
from __future__ import annotations

import argparse
import json
import random
from pathlib import Path


ALLOWED_COUNTS = {10_000, 100_000, 1_000_000}


def records(count: int, seed: int = 26):
    if count < 3:
        raise ValueError("count must be at least 3 to create linked factory objects")
    rng = random.Random(seed)
    for index in range(count):
        kind = ("Supplier", "Material", "WorkOrder", "WorkCenter")[index % 4]
        record = {"id": f"{kind}-{index:09d}", "type": kind, "properties": {}}
        if kind == "Supplier":
            record["properties"] = {"lead_time_days": rng.randint(1, 30), "active": True}
        elif kind == "Material":
            record["properties"] = {"quantity_on_hand": rng.randint(0, 5000), "unit": "kg"}
            record["links"] = [{"type": "SOURCED_FROM", "target": f"Supplier-{(index - 1) % count:09d}"}]
        elif kind == "WorkOrder":
            record["properties"] = {"quantity": rng.randint(1, 500), "status": "planned"}
            record["links"] = [{"type": "REQUIRES", "target": f"Material-{(index - 1) % count:09d}"}]
            record["links"].append({"type": "ASSIGNED_TO", "target": f"WorkCenter-{(index + 1) % count:09d}"})
        else:
            record["properties"] = {"available_minutes": rng.randint(240, 1440), "line": f"line-{index % 80:03d}"}
        yield record


def write_fixture(destination: Path, count: int, seed: int = 26) -> None:
    if count not in ALLOWED_COUNTS:
        raise ValueError(f"count must be one of {sorted(ALLOWED_COUNTS)}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open("w", encoding="utf-8", newline="\n") as output:
        for record in records(count, seed):
            output.write(json.dumps(record, sort_keys=True, separators=(",", ":")))
            output.write("\n")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--count", type=int, choices=sorted(ALLOWED_COUNTS), required=True)
    parser.add_argument("--seed", type=int, default=26)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    write_fixture(args.output, args.count, args.seed)
    print(f"wrote {args.count} synthetic objects (seed={args.seed}) to {args.output}")


if __name__ == "__main__":
    main()
