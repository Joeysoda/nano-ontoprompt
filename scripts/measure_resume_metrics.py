"""Reproduce resume-scale measurements without changing application data.

Examples, from the repository root:

    python scripts/measure_resume_metrics.py --repo --live-db --live-graph
    python scripts/measure_resume_metrics.py --collect-backend-tests --list-frontend-tests
    python -m pytest -q  # run from backend/ to measure pass/fail/skip

The database and graph commands issue SELECT/GRAPH.LIST only. The script does
not start services, seed fixtures, run migrations, or write to the database.
"""
from __future__ import annotations

import argparse
import ast
import json
import os
from pathlib import Path
import re
import subprocess
import sys
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
BACKEND = ROOT / "backend"
FRONTEND = ROOT / "frontend"


def _run(command: list[str], cwd: Path) -> str:
    completed = subprocess.run(
        command,
        cwd=cwd,
        check=True,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    return completed.stdout


def _decorator_name(node: ast.AST) -> str | None:
    if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
        return None
    owner = node.func.value
    if isinstance(owner, ast.Name) and owner.id == "router" and node.func.attr in {
        "get", "post", "put", "patch", "delete", "options", "head"
    }:
        return node.func.attr.upper()
    return None


def agent_surface() -> dict[str, Any]:
    service_path = BACKEND / "app/services/v2/agent_decision.py"
    router_path = BACKEND / "app/routers/v2/agent.py"
    service_tree = ast.parse(service_path.read_text(encoding="utf-8"))
    router_tree = ast.parse(router_path.read_text(encoding="utf-8"))

    tools: list[str] = []
    for node in ast.walk(service_tree):
        if not isinstance(node, ast.Call):
            continue
        for keyword in node.keywords:
            if keyword.arg != "tools" or not isinstance(keyword.value, (ast.List, ast.Tuple)):
                continue
            found = []
            for value in keyword.value.elts:
                if (isinstance(value, ast.Attribute) and isinstance(value.value, ast.Name)
                        and value.value.id == "toolbox"):
                    found.append(value.attr)
            if found:
                tools = found

    routes = []
    for node in ast.walk(router_tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            method = _decorator_name(decorator)
            if method and isinstance(decorator, ast.Call) and decorator.args:
                path = ast.literal_eval(decorator.args[0])
                routes.append({"method": method, "path": path, "handler": node.name})

    return {
        "agent_tools_exposed_to_agno": len(tools),
        "agent_tool_names": tools,
        "agent_http_endpoints": len(routes),
        "agent_routes": sorted(routes, key=lambda row: (row["path"], row["method"])),
    }


def fixture_metrics() -> dict[str, Any]:
    sys.path.insert(0, str(BACKEND))
    from app.services.v2.manufacturing_data import adapt  # noqa: PLC0415

    frepple_path = ROOT / "data/frepple_demo/manufacturing_demo.json"
    report = adapt(frepple_path.read_bytes())
    uci_manifest = json.loads(
        (BACKEND / "tests/data/uci_steel_energy_manifest.json").read_text(encoding="utf-8")
    )
    return {
        "frepple_reviewed_import_fixture": {
            "source_records": report["source_records"],
            "objects": len(report["nodes"]),
            "relationships": len(report["edges"]),
            "excluded_records": len(report["excluded_records"]),
            "demand_statuses": report["demand_statuses"],
            "sha256": report["sha256"],
        },
        "uci_steel_reviewed_repo_fixture": {
            "rows": uci_manifest["rows"],
            "interval_minutes": uci_manifest["timestamp_interval_minutes"],
            "columns": len(uci_manifest["columns"]),
            "sha256": uci_manifest["csv_sha256"],
            "note": "Repository fixture processed by logic; not rows in the native time-series tables.",
        },
    }


AGGREGATE_SQL = """
SELECT 'connections', count(*) FROM v2_connections
UNION ALL SELECT 'dataset_resources', count(*) FROM v2_datasets
UNION ALL SELECT 'entity_instances', count(*) FROM entity_instances
UNION ALL SELECT 'object_type_definitions', count(*) FROM entities
UNION ALL SELECT 'ontology_projects', count(*) FROM ontology_projects
UNION ALL SELECT 'relationship_type_definitions', count(*) FROM relations
UNION ALL SELECT 'temporal_facts', count(*) FROM v2_temporal_facts
UNION ALL SELECT 'temporal_stream_events', count(*) FROM v2_temporal_stream_events
UNION ALL SELECT 'time_series_syncs', count(*) FROM v2_time_series_syncs
UNION ALL SELECT 'uploaded_files', count(*) FROM uploaded_files
ORDER BY 1;
"""

SCOPED_SQL = """
SELECT p.id, p.name, p.domain,
       count(DISTINCT e.id) AS object_types,
       count(DISTINCT i.id) AS entity_instances,
       count(DISTINCT r.id) AS relationship_types
FROM ontology_projects p
LEFT JOIN entities e ON e.ontology_id=p.id
LEFT JOIN entity_instances i ON i.ontology_id=p.id
LEFT JOIN relations r ON r.ontology_id=p.id
WHERE p.id='frepple-d7eb98078882b234c395fd05'
   OR p.name IN ('azure_pdm_db_test2','演示2')
GROUP BY p.id,p.name,p.domain
ORDER BY entity_instances DESC;
"""

IMPORT_REPORT_SQL = """
SELECT id,
       jsonb_array_length((config::jsonb)->'report'->'nodes') AS report_nodes,
       jsonb_array_length((config::jsonb)->'report'->'edges') AS report_edges,
       ((config::jsonb)->'report'->>'source_records')::int AS source_records,
       jsonb_array_length((config::jsonb)->'report'->'excluded_records') AS excluded_records
FROM v2_construction_runs
WHERE ontology_id='frepple-d7eb98078882b234c395fd05';
"""

TELEMETRY_SQL = """
SELECT coalesce(e.name_en,e.name_cn) AS object_type,
       count(*) AS timestamped_records,
       sum(CASE
             WHEN (i.row_data::jsonb ? 'voltage')
              AND (i.row_data::jsonb ? 'rotationSpeed')
              AND (i.row_data::jsonb ? 'pressureValue')
              AND (i.row_data::jsonb ? 'vibrationLevel') THEN 4
             WHEN (i.row_data::jsonb ? 'volt')
              AND (i.row_data::jsonb ? 'rotate')
              AND (i.row_data::jsonb ? 'pressure')
              AND (i.row_data::jsonb ? 'vibration') THEN 4
             ELSE 0
           END) AS scalar_measurements,
       count(DISTINCT coalesce(i.row_data::jsonb->>'machineId',
                               i.row_data::jsonb->>'machineID')) AS machines
FROM entity_instances i
JOIN entities e ON e.id=i.entity_id
WHERE coalesce(e.name_en,e.name_cn) IN ('TelemetryReading','PdMTelemetryRecord')
GROUP BY object_type
ORDER BY object_type;
"""


def psql(sql: str) -> str:
    return _run(
        ["docker", "compose", "exec", "-T", "db", "psql", "-U", "ontoprompt",
         "-d", "ontoprompt", "-P", "pager=off", "-c", sql],
        ROOT,
    )


def live_database() -> dict[str, str]:
    return {
        "aggregate_counts": psql(AGGREGATE_SQL),
        "scoped_ontologies": psql(SCOPED_SQL),
        "frepple_import_report": psql(IMPORT_REPORT_SQL),
        "telemetry_rows_and_scalar_measurements": psql(TELEMETRY_SQL),
    }


def live_graph() -> dict[str, Any]:
    output = _run(["docker", "compose", "exec", "-T", "falkordb", "redis-cli", "GRAPH.LIST"], ROOT)
    names = [line.strip() for line in output.splitlines() if line.strip()]
    return {"named_graphs": len(names), "graph_names": names}


def collect_backend_tests() -> dict[str, Any]:
    output = _run([sys.executable, "-m", "pytest", "--collect-only", "-q"], BACKEND)
    last = next((line.strip() for line in reversed(output.splitlines()) if "collected" in line), "")
    match = re.search(r"(\d+) tests? collected", last)
    return {"pytest_summary": last, "collected": int(match.group(1)) if match else None}


def list_frontend_tests() -> dict[str, Any]:
    executable = "npm.cmd" if os.name == "nt" else "npm"
    output = _run([executable, "exec", "playwright", "test", "--", "--list"], FRONTEND)
    last = next((line.strip() for line in reversed(output.splitlines()) if line.startswith("Total:")), "")
    match = re.search(r"Total: (\d+) tests in (\d+) files", last)
    return {
        "playwright_summary": last,
        "collected": int(match.group(1)) if match else None,
        "files": int(match.group(2)) if match else None,
    }


def main() -> None:
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    parser = argparse.ArgumentParser()
    parser.add_argument("--repo", action="store_true", help="Measure agent surface and reviewed fixtures")
    parser.add_argument("--live-db", action="store_true", help="Run read-only SELECT counts through Docker")
    parser.add_argument("--live-graph", action="store_true", help="Run read-only GRAPH.LIST through Docker")
    parser.add_argument("--collect-backend-tests", action="store_true", help="Collect pytest cases without running them")
    parser.add_argument("--list-frontend-tests", action="store_true", help="List Playwright cases without running them")
    args = parser.parse_args()
    if not any(vars(args).values()):
        parser.error("select at least one measurement option")

    result: dict[str, Any] = {}
    if args.repo:
        result["agent_surface"] = agent_surface()
        result["reviewed_fixtures"] = fixture_metrics()
    if args.live_db:
        result["live_database_raw"] = live_database()
    if args.live_graph:
        result["live_graph"] = live_graph()
    if args.collect_backend_tests:
        result["backend_tests"] = collect_backend_tests()
    if args.list_frontend_tests:
        result["frontend_tests"] = list_frontend_tests()
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
