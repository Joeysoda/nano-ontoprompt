"""Bounded adapters for the project regression datasets.

Adapters only normalize columns; they never manufacture a timestamp when a
source contains ordinal cycles or an invalid time value.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class TemporalAdapter:
    name: str
    time_kind: str
    description: str
    normalize: Callable[[list[dict[str, Any]]], list[dict[str, Any]]]


def _cmapss(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        if "unit_id" not in item:
            item["unit_id"] = item.get("unit") or item.get("engine_id")
        if "event_seq" not in item:
            item["event_seq"] = item.get("cycle")
        result.append(item)
    return result


def _scania(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    result = []
    for row in rows:
        item = dict(row)
        if "unit_id" not in item:
            item["unit_id"] = item.get("component_id") or item.get("truck_id")
        if "event_time" not in item:
            item["event_time"] = item.get("timestamp") or item.get("time")
        result.append(item)
    return result


ADAPTERS = {
    "cmapss": TemporalAdapter("cmapss", "ordinal", "NASA C-MAPSS cycle is sequence time; no calendar date is inferred.", _cmapss),
    "c-mapss": TemporalAdapter("cmapss", "ordinal", "NASA C-MAPSS cycle is sequence time; no calendar date is inferred.", _cmapss),
    "scania": TemporalAdapter("scania", "instant", "SCANIA component records use source timestamps normalized by the temporal service.", _scania),
}


def get_adapter(name: str) -> TemporalAdapter:
    try:
        return ADAPTERS[name.lower()]
    except KeyError as exc:
        raise ValueError(f"unsupported temporal dataset adapter: {name}") from exc


def list_adapters() -> list[dict[str, str]]:
    seen = set()
    items = []
    for adapter in ADAPTERS.values():
        if adapter.name in seen:
            continue
        seen.add(adapter.name)
        items.append({"name": adapter.name, "time_kind": adapter.time_kind, "description": adapter.description})
    return items
