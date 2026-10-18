"""Batch adapter: Azure Predictive Maintenance sample -> canonical Machinery surface.

This is a demo/acceptance adapter, deliberately separate from generic ingest
APIs (Plan B invariant 12).  It produces exactly the contract that
``temporal_surface.install_surface`` installs: Event Objects with
start/end timestamps linked to their Machine, and one Time Series Property
per telemetry channel resolved through ``TimeSeriesSync``.

Telemetry readings are TSP points, not objects.  Failures, maintenance and
errors are discrete events and therefore Event Objects.

Source: Microsoft Azure AI predictive maintenance sample (simulated data).
Timestamps carry no zone in the source; they are interpreted as UTC.
"""
from __future__ import annotations

import csv
import hashlib
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime, timezone
from math import isfinite
from pathlib import Path

CHANNELS = {"volt": "V", "rotate": "rpm", "pressure": "psi", "vibration": "mm/s"}
EVENT_FILES = {"FailureEvent": ("PdM_failures.csv", "failure"), "MaintenanceEvent": ("PdM_maint.csv", "comp"),
               "ErrorEvent": ("PdM_errors.csv", "errorID")}
# One link type per event type: link API names must identify a single (source, target) pair.
EVENT_LINKS = {"FailureEvent": "FAILURE_OF", "MaintenanceEvent": "MAINTENANCE_OF", "ErrorEvent": "ERROR_OF"}


class PdmError(ValueError):
    pass


@dataclass
class PdmSurface:
    machines: list[dict]
    event_nodes: list[dict]
    event_links: list[dict]
    syncs: list[dict]
    manifest: dict


def machine_id(raw: str) -> str:
    return f"machine-{int(raw):03d}"


def _utc(value: str, path: str) -> str:
    try:
        parsed = datetime.strptime(value, "%Y-%m-%d %H:%M:%S")
    except ValueError as exc:
        raise PdmError(f"{path}: invalid timestamp {value!r}") from exc
    return parsed.replace(tzinfo=timezone.utc).isoformat()


def _rows(directory: Path, name: str) -> tuple[list[dict], dict]:
    path = directory / name
    raw = path.read_bytes()
    rows = list(csv.DictReader(raw.decode("utf-8-sig").splitlines()))
    return rows, {"file": name, "sha256": hashlib.sha256(raw).hexdigest(), "bytes": len(raw), "rows": len(rows)}


def series_id(machine: str, channel: str) -> str:
    return f"series:pdm:{machine}:{channel}"


def load_surface(directory: str | Path, machine_limit: int | None = None) -> PdmSurface:
    directory = Path(directory)
    manifest = {"source": "Azure AI predictive maintenance sample (simulated)", "timezone_assumption": "UTC", "files": []}
    machine_rows, info = _rows(directory, "PdM_machines.csv")
    manifest["files"].append(info)
    machines, allowed = [], set()
    for row in sorted(machine_rows, key=lambda r: int(r["machineID"])):
        if machine_limit is not None and len(machines) >= machine_limit:
            break
        mid = machine_id(row["machineID"])
        if mid in allowed:
            raise PdmError(f"duplicate machine {mid}")
        allowed.add(mid)
        machines.append({"id": mid, "entity_type": "Machine", "properties": {
            "model": row["model"], "age": int(row["age"]), "maintenance_status": "normal",
            **{f"{channel}_series": series_id(mid, channel) for channel in CHANNELS}}})

    event_nodes, event_links = [], []
    for event_type, (name, field) in EVENT_FILES.items():
        rows, info = _rows(directory, name)
        manifest["files"].append(info)
        seen = set()
        for index, row in enumerate(rows):
            mid = machine_id(row["machineID"])
            if mid not in allowed:
                continue
            stamp = _utc(row["datetime"], f"{name}:{index}")
            key = f"{event_type}:{mid}:{stamp}:{row[field]}"
            if key in seen:
                raise PdmError(f"{name}: duplicate event {key}")
            seen.add(key)
            node_id = "event:" + hashlib.sha256(key.encode()).hexdigest()[:40]
            event_nodes.append({"id": node_id, "entity_type": event_type, "properties": {
                "start_timestamp": stamp, "end_timestamp": stamp, "event_key": key, "code": row[field], "machine": mid}})
            event_links.append({"source": node_id, "target": mid, "type": EVENT_LINKS[event_type]})

    rows, info = _rows(directory, "PdM_telemetry.csv")
    manifest["files"].append(info)
    points: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for index, row in enumerate(rows):
        mid = machine_id(row["machineID"])
        if mid not in allowed:
            continue
        stamp = _utc(row["datetime"], f"PdM_telemetry.csv:{index}")
        for channel in CHANNELS:
            value = float(row[channel])
            if not isfinite(value):
                raise PdmError(f"PdM_telemetry.csv:{index}: non-finite {channel}")
            points[(mid, channel)].append({"timestamp": stamp, "value": value})
    syncs = []
    for (mid, channel), series in sorted(points.items()):
        series.sort(key=lambda p: p["timestamp"])
        if len({p["timestamp"] for p in series}) != len(series):
            raise PdmError(f"{mid}/{channel}: duplicate timestamps")
        syncs.append({"root_type": "Machine", "root_id": mid, "sensor_type": "TelemetryChannel", "sensor_id": f"{mid}:{channel}",
                      "series_id": series_id(mid, channel), "property_api_name": f"{channel}_series", "unit": CHANNELS[channel],
                      "interpolation": "linear", "points": series})
    manifest["counts"] = {"machines": len(machines), "events": len(event_nodes), "series": len(syncs),
                          "points": sum(len(s["points"]) for s in syncs)}
    return PdmSurface(machines, event_nodes, event_links, syncs, manifest)


def ontology_types() -> list[dict]:
    """Entity definitions for load_sql_metadata (object types and event types)."""
    machine = [{"id": "model", "type": "string"}, {"id": "age", "type": "integer"}, {"id": "maintenance_status", "type": "string"}]
    machine += [{"id": f"{channel}_series", "type": "string"} for channel in CHANNELS]
    event = [{"id": "start_timestamp", "type": "datetime"}, {"id": "end_timestamp", "type": "datetime"},
             {"id": "event_key", "type": "string"}, {"id": "code", "type": "string"}, {"id": "machine", "type": "string"}]
    return [{"api_name": "Machine", "name_cn": "设备", "properties": machine}] + [
        {"api_name": name, "name_cn": name, "properties": event} for name in EVENT_FILES]
