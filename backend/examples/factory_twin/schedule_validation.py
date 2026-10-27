"""Generic schedule-validation/v1 rules over bound fields and row attributes."""
from datetime import datetime


def _dt(value):
    return datetime.fromisoformat(str(value).replace("Z", "+00:00"))


def run(inputs):
    rows = inputs.get("schedule_objects") or []
    by_id = {row.get("id"): row for row in rows}
    violations = []
    for row in rows:
        task_id, attrs = row.get("id"), row.get("attributes") or {}
        if row.get("layer_id") == "constraint":
            continue
        resources = row.get("container_ids") or []
        compatible = attrs.get("compatible_resources") or resources
        if isinstance(compatible, str):
            compatible = [compatible]
        bad = sorted(set(resources) - set(compatible))
        if bad:
            violations.append({"code": "incompatible_resource", "severity": "error", "task_ids": [task_id],
                               "message": "%s uses an incompatible resource: %s" % (row.get("label"), ", ".join(bad))})
        if attrs.get("material_ready") is False:
            violations.append({"code": "material_not_ready", "severity": "error", "task_ids": [task_id],
                               "message": "%s cannot start until material is released" % row.get("label")})
        if attrs.get("quality_hold") is True:
            violations.append({"code": "quality_hold", "severity": "error", "task_ids": [task_id],
                               "message": "%s is blocked by an active quality hold" % row.get("label")})
        for predecessor_id in row.get("predecessor_ids") or []:
            predecessor = by_id.get(predecessor_id)
            if predecessor and predecessor.get("end") and row.get("start") and _dt(predecessor["end"]) > _dt(row["start"]):
                violations.append({"code": "precedence", "severity": "error", "task_ids": [predecessor_id, task_id],
                                   "message": "%s starts before predecessor %s completes" % (row.get("label"), predecessor.get("label"))})
    production = [row for row in rows if row.get("layer_id") == "production" and row.get("start") and row.get("end")]
    for index, left in enumerate(production):
        for right in production[index + 1:]:
            shared = set(left.get("container_ids") or []) & set(right.get("container_ids") or [])
            if shared and _dt(left["start"]) < _dt(right["end"]) and _dt(right["start"]) < _dt(left["end"]):
                violations.append({"code": "capacity_collision", "severity": "error", "task_ids": [left["id"], right["id"]],
                                   "message": "Production capacity collision on %s" % ", ".join(sorted(shared))})
    return {"schema": "schedule-validation/v1", "violations": violations}
