"""Stage-1 evaluation for the Semantica reasoning engines.

This script deliberately lives outside the application runtime. It checks the
pinned engine against either the repository's checked-in BTS observations or
the external Azure baseline. Run it before changing the selected revision::

    python scripts/evaluate_semantica_reasoning.py \
      --semantica-src <path-to-semantica> \
      --bts-observations data/bts_demo/observations.csv
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
import time
from pathlib import Path


def _arguments() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    parser.add_argument("--semantica-src", type=Path, required=True)
    parser.add_argument("--azure-machines", type=Path)
    parser.add_argument("--bts-observations", type=Path)
    return parser.parse_args()


def _load_machine_ids(path: Path) -> list[str]:
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    machine_ids = [str(row["machineID"]).strip() for row in rows]
    if len(machine_ids) != 100 or len(set(machine_ids)) != 100:
        raise AssertionError(
            f"Expected 100 unique Azure machines, found {len(machine_ids)} rows "
            f"and {len(set(machine_ids))} unique IDs"
        )
    return machine_ids


def _load_bts_ids(path: Path) -> list[str]:
    """Use the repository's checked-in BTS observations for a real-data run."""
    with path.open(newline="", encoding="utf-8-sig") as handle:
        rows = list(csv.DictReader(handle))
    ids = sorted({str(row["stream_id"]).strip() for row in rows if row.get("stream_id")})
    if not ids:
        raise AssertionError("BTS observations contain no stream_id values")
    return ids


def _reasoner_checks(machine_ids: list[str]) -> dict[str, object]:
    from semantica.reasoning import Reasoner

    line_for = {machine_id: ((index // 10) + 1) for index, machine_id in enumerate(machine_ids)}
    facts = [f"Equipment(m{machine_id})" for machine_id in machine_ids]
    facts.extend(
        f"covers(m{machine_id}, pl{line_for[machine_id]:02d})"
        for machine_id in machine_ids
    )
    rules = [
        "IF covers(?machine, ?line) THEN assigned(?machine, ?line)",
        "IF assigned(?machine, ?line) THEN monitored_line(?line)",
    ]
    started = time.perf_counter()
    results = Reasoner(max_iterations=50).infer_with_results(facts, rules)
    elapsed = time.perf_counter() - started
    conclusions = {result.conclusion for result in results}
    expected_assignments = {
        f"assigned(m{machine_id}, pl{line_for[machine_id]:02d})"
        for machine_id in machine_ids
    }
    expected_lines = {f"monitored_line(pl{line:02d})" for line in set(line_for.values())}

    cycle_started = time.perf_counter()
    cycle_results = Reasoner(max_iterations=50).infer_with_results(
        ["state_a(m1)"],
        [
            "IF state_a(?machine) THEN state_b(?machine)",
            "IF state_b(?machine) THEN state_a(?machine)",
        ],
    )
    cycle_elapsed = time.perf_counter() - cycle_started

    alternative_results = Reasoner().infer_with_results(
        ["sensor_alarm(m1)", "maintenance_overdue(m1)"],
        [
            "IF sensor_alarm(?machine) THEN needs_inspection(?machine)",
            "IF maintenance_overdue(?machine) THEN needs_inspection(?machine)",
        ],
    )
    alternative = next(
        result
        for result in alternative_results
        if result.conclusion == "needs_inspection(m1)"
    )

    malformed_reasoner = Reasoner()
    malformed_raised = False
    try:
        malformed_rule = malformed_reasoner.add_rule("state_a(?x) -> state_b(?x)")
    except Exception:
        malformed_raised = True
        malformed_rule = None

    return {
        "input_fact_count": len(facts),
        "derived_fact_count": len(conclusions),
        "assignment_accuracy": {
            "expected": len(expected_assignments),
            "matched": len(conclusions & expected_assignments),
        },
        "multistep_accuracy": {
            "expected": len(expected_lines),
            "matched": len(conclusions & expected_lines),
        },
        "elapsed_seconds": round(elapsed, 6),
        "has_per_result_rule": all(result.rule_used is not None for result in results),
        "has_per_result_premises": all(bool(result.premises) for result in results),
        "cycle": {
            "elapsed_seconds": round(cycle_elapsed, 6),
            "conclusions": [result.conclusion for result in cycle_results],
            "terminated": cycle_elapsed < 1.0,
        },
        "alternative_derivations": {
            "result_count_for_conclusion": sum(
                result.conclusion == "needs_inspection(m1)"
                for result in alternative_results
            ),
            "retained_rule_id": alternative.rule_used.rule_id,
            "merged_premises": sorted(alternative.premises),
            "preserves_two_independent_proofs": False,
        },
        "malformed_rule": {
            "raised": malformed_raised,
            "accepted_condition_count": (
                None if malformed_rule is None else len(malformed_rule.conditions)
            ),
        },
    }


def _datalog_checks(machine_ids: list[str]) -> dict[str, object]:
    from semantica.reasoning import DatalogReasoner

    reasoner = DatalogReasoner()
    line_for = {machine_id: ((index // 10) + 1) for index, machine_id in enumerate(machine_ids)}
    for machine_id in machine_ids:
        line = line_for[machine_id]
        reasoner.add_fact(f"covers(m{machine_id}, pl{line:02d})")
    reasoner.add_rule("peer(X, Y) :- covers(X, L), covers(Y, L).")

    started = time.perf_counter()
    incorrect: list[str] = []
    for machine_id in machine_ids:
        line = line_for[machine_id]
        result = reasoner.query(f"peer(m{machine_id}, ?Y)")
        actual = {binding["Y"] for binding in result} - {f"m{machine_id}"}
        expected = {f"m{other}" for other in machine_ids if line_for[other] == line} - {f"m{machine_id}"}
        if actual != expected:
            incorrect.append(machine_id)
    elapsed = time.perf_counter() - started

    recursive = DatalogReasoner()
    recursive.add_fact("depends(a, b)")
    recursive.add_fact("depends(b, c)")
    recursive.add_fact("depends(c, a)")
    recursive.add_rule("impacts(X, Y) :- depends(X, Y).")
    recursive.add_rule("impacts(X, Z) :- depends(X, Y), impacts(Y, Z).")
    recursive_started = time.perf_counter()
    recursive_result = recursive.query("impacts(a, ?Z)")
    recursive_elapsed = time.perf_counter() - recursive_started

    malformed_raised = False
    try:
        DatalogReasoner().add_rule("state_a(X) -> state_b(X)")
    except Exception:
        malformed_raised = True

    return {
        "peer_queries": len(machine_ids),
        "correct_queries": len(machine_ids) - len(incorrect),
        "queries_per_second": round(len(machine_ids) / elapsed, 2),
        "incorrect_machine_ids": incorrect[:5],
        "recursive_cycle": {
            "elapsed_seconds": round(recursive_elapsed, 6),
            "targets": sorted(binding["Z"] for binding in recursive_result),
            "terminated": recursive_elapsed < 1.0,
        },
        "malformed_rule_raised": malformed_raised,
        "proof_objects_available": False,
    }


def main() -> None:
    args = _arguments()
    semantica_src = args.semantica_src.resolve()
    if not (semantica_src / "semantica" / "reasoning").is_dir():
        raise SystemExit(f"Not a Semantica source checkout: {semantica_src}")
    sys.path.insert(0, str(semantica_src))

    if bool(args.azure_machines) == bool(args.bts_observations):
        raise SystemExit("Pass exactly one of --azure-machines or --bts-observations")
    source_path = (args.azure_machines or args.bts_observations).resolve()
    machine_ids = _load_machine_ids(source_path) if args.azure_machines else _load_bts_ids(source_path)
    report = {
        "dataset": {
            "path": str(source_path),
            "machine_count": len(machine_ids),
            "source_kind": "azure_pdm_external_baseline" if args.azure_machines else "joey_bts_checked_in",
            "line_grouping": "demo-only deterministic groups of 10 machines" if args.azure_machines else "BTS stream identities",
        },
        "reasoner": _reasoner_checks(machine_ids),
        "datalog_reasoner": _datalog_checks(machine_ids),
    }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
