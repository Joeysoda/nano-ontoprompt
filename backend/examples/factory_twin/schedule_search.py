"""Generic gap search for any schedule-search/v1 Workshop binding.

The function knows only the public scheduling contract. Domain-specific
compatibility is supplied as data on each schedulable object.
"""
from datetime import datetime, timedelta, timezone


def _dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def _iso(value):
    return value.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def run(inputs):
    rows = inputs.get("schedule_objects") or []
    trigger = (inputs.get("trigger") or {}).get("schedulable_object_primary_key")
    selected = next((row for row in rows if row.get("id") == trigger), None)
    if not selected:
        return {"schema": "schedule-search/v1", "error": "The selected schedule object is unavailable"}
    domain_start, domain_end = _dt(inputs["domain_start"]), _dt(inputs["domain_end"])
    if selected.get("start") and selected.get("end"):
        duration = _dt(selected["end"]) - _dt(selected["start"])
    else:
        duration = timedelta(hours=1)
    attrs = selected.get("attributes") or {}
    compatible = attrs.get("compatible_resources") or selected.get("container_ids") or []
    if isinstance(compatible, str):
        compatible = [compatible]
    occupied = {}
    for row in rows:
        if row.get("id") == trigger or not row.get("start") or not row.get("end") or row.get("layer_id") == "constraint":
            continue
        for resource in row.get("container_ids") or ([row.get("container_id")] if row.get("container_id") else []):
            occupied.setdefault(resource, []).append((_dt(row["start"]), _dt(row["end"]), row.get("id")))
    highlights, blockers = [], set()
    for resource in compatible:
        cursor = domain_start
        for start, end, object_id in sorted(occupied.get(resource, [])):
            if end <= cursor:
                continue
            if start - cursor >= duration:
                highlights.append({"type": "SLOT", "container_id": resource,
                                   "domain": {"start": _iso(cursor), "end": _iso(cursor + duration)},
                                   "comment": "Earliest compatible gap without a resource collision"})
                break
            if start < cursor + duration and end > cursor:
                blockers.add(object_id)
            cursor = max(cursor, end)
        else:
            if cursor + duration <= domain_end:
                highlights.append({"type": "SLOT", "container_id": resource,
                                   "domain": {"start": _iso(cursor), "end": _iso(cursor + duration)},
                                   "comment": "Compatible gap after the current sequence"})
        if len(highlights) >= 4:
            break
    highlights.extend({"type": "PUCK", "schedulable_object_primary_key": object_id,
                       "comment": "This task occupies a candidate interval"} for object_id in sorted(blockers)[:4])
    return {"schema": "schedule-search/v1", "source_puck_ids": sorted(blockers),
            "row_group": {"title": "Constraint-aware alternatives", "container_ids": list(compatible),
                          "highlights": highlights}}
