"""Deterministic material-shortage decision model.

This module is deliberately independent of persistence.  A Scenario Run pins the
input revision; this function turns that immutable input into an explainable
artifact that can later be reviewed and applied through Actions.

Semantics (all deterministic, no optimality claim):

* Orders are fulfilled all-or-nothing.  Material is committed to an order only
  if every BOM leaf is covered; otherwise the tentative allocation is rolled
  back so scarce material is not stranded on an order that cannot ship.
* The baseline uses reserved stock, free stock and active, unexpired supply
  commitments.  Purchase options are not part of the baseline: buying from an
  alternate source is a candidate action whose cost and effect are evaluated.
* Each candidate is evaluated by re-running the same simulation on a
  transformed input, so candidate numbers and baseline numbers share one code
  path and are reproducible.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from hashlib import sha256
import json
from math import ceil, inf
from typing import Any


EPSILON = 1e-9
MAX_CANDIDATES = 25


def _number(value: Any, label: str) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"{label} must be numeric") from exc
    if result < 0:
        raise ValueError(f"{label} cannot be negative")
    return result


def _time(value: Any, label: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError(f"{label} must be an ISO datetime") from exc
    return parsed


def _stable_digest(payload: dict) -> str:
    encoded = json.dumps(payload, ensure_ascii=False, sort_keys=True, separators=(",", ":"), allow_nan=False)
    return sha256(encoded.encode("utf-8")).hexdigest()


def _at(value: Any, anchor: datetime, label: str) -> datetime:
    """Numbers are day offsets from the scenario time; strings are ISO datetimes."""
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return anchor + timedelta(days=float(value))
    return _time(value, label)


def _earliest_slot(busy: list[tuple[datetime, datetime]], ready: datetime, duration: timedelta) -> datetime:
    start = ready
    moved = True
    while moved:
        moved = False
        for busy_start, busy_end in busy:
            if start < busy_end and start + duration > busy_start:
                start, moved = busy_end, True
    return start


def _prepare(inputs: dict) -> dict:
    """Validate the input once; simulations only read the prepared structure."""
    anchor = _time(inputs["scenario_time"], "scenario_time")
    demands = list(inputs.get("demands", []))
    boms = list(inputs.get("boms", []))
    if len(demands) > 500 or len(boms) > 2_000:
        raise ValueError("material-shortage scenario exceeds bounded input size")

    bom: dict[str, list[tuple[str, float]]] = defaultdict(list)
    for row in boms:
        parent = str(row.get("parent_item", ""))
        component = str(row.get("component_item", ""))
        quantity = _number(row.get("quantity_per"), "BOM quantity_per")
        if not parent or not component or quantity <= 0:
            raise ValueError("BOM rows require parent_item, component_item and positive quantity_per")
        bom[parent].append((component, quantity))
    for children in bom.values():
        children.sort()

    def explode(item: str, quantity: float, path: tuple[str, ...] = ()) -> list[dict]:
        if item in path:
            raise ValueError(f"BOM cycle detected at item {item}")
        if len(path) >= 20:
            raise ValueError("BOM expansion exceeded depth 20")
        children = bom.get(item, [])
        if not children:
            return [{"item": item, "quantity": quantity, "path": list(path + (item,))}]
        merged: dict[tuple[str, tuple[str, ...]], float] = defaultdict(float)
        for child, factor in children:
            for leaf in explode(child, quantity * factor, path + (item,)):
                merged[(leaf["item"], tuple(leaf["path"]))] += leaf["quantity"]
        return [{"item": key[0], "path": list(key[1]), "quantity": value} for key, value in sorted(merged.items())]

    stock = []
    for index, row in enumerate(inputs.get("inventory", [])):
        item = str(row.get("item", row.get("item_id", "")))
        if not item:
            raise ValueError("inventory rows require item")
        stock.append({"id": str(row.get("id", f"inventory:{index}")), "item": item,
                      "location": str(row.get("location", "default")),
                      "quantity": _number(row.get("quantity", row.get("onhand", 0)), "inventory quantity")})
    stock.sort(key=lambda row: (row["item"], row["location"], row["id"]))

    reservations = []
    for index, row in enumerate(inputs.get("reservations", [])):
        if row.get("status", "active") != "active":
            continue
        item, demand_id = str(row.get("item", "")), str(row.get("demand_id", ""))
        if not item or not demand_id:
            raise ValueError("active reservations require item and demand_id")
        reservations.append({"id": str(row.get("id", f"reservation:{index}")), "item": item, "demand_id": demand_id,
                             "quantity": _number(row.get("quantity", 0), "reservation quantity")})
    for item in {row["item"] for row in reservations}:
        reserved = sum(row["quantity"] for row in reservations if row["item"] == item)
        if reserved > sum(row["quantity"] for row in stock if row["item"] == item) + EPSILON:
            raise ValueError(f"active reservations exceed inventory for item {item}")

    commitments = []
    for index, row in enumerate(inputs.get("supply_commitments", [])):
        if row.get("status", "active") != "active":
            continue
        expires_at = _time(row["expires_at"], "commitment expires_at") if row.get("expires_at") else None
        if expires_at and expires_at < anchor:
            continue
        item = str(row.get("item", ""))
        if not item:
            raise ValueError("supply commitments require item")
        expedite_at = row.get("expedite_available_at")
        commitments.append({
            "id": str(row.get("id", f"commitment:{index}")), "kind": "commitment", "supplier": row.get("supplier"), "item": item,
            "quantity": _number(row.get("quantity", 0), "commitment quantity"),
            "available_at": _at(row.get("available_at"), anchor, "commitment available_at"),
            "unit_cost": _number(row.get("unit_cost", 0), "commitment unit_cost"),
            "expedite_available_at": _at(expedite_at, anchor, "expedite_available_at") if expedite_at is not None else None,
            "expedite_unit_premium": _number(row.get("expedite_unit_premium", 0), "expedite_unit_premium"),
        })

    options = []
    for index, row in enumerate(inputs.get("supply_options", [])):
        if row.get("disabled"):
            continue
        item = str(row.get("item", ""))
        if not item:
            raise ValueError("supply options require item")
        lead_days = _number(row.get("lead_time", row.get("lead_days", 0)), "lead_time")
        available_at = anchor + timedelta(days=lead_days)
        if row.get("outage_until"):
            available_at = max(available_at, _time(row["outage_until"], "outage_until") + timedelta(days=lead_days))
        options.append({
            "id": str(row.get("id", f"option:{index}")), "kind": "purchase_option", "supplier": row.get("supplier"), "item": item,
            "quantity": _number(row.get("quantity", row.get("capacity", 0)), "supply quantity"),
            "available_at": available_at, "unit_cost": _number(row.get("unit_cost", row.get("cost", 0)), "unit_cost"),
            "qualified": row.get("qualified", True) is not False,
        })

    fixed_tasks = []
    for index, row in enumerate(inputs.get("fixed_tasks", [])):
        start, finish = _at(row.get("start"), anchor, "fixed task start"), _at(row.get("finish"), anchor, "fixed task finish")
        if finish <= start:
            raise ValueError("fixed tasks require finish after start")
        fixed_tasks.append({"id": str(row.get("id", f"fixed:{index}")), "resource": str(row.get("resource", "default")),
                            "start": start, "finish": finish})

    prepared_demands = []
    for demand in demands:
        demand_id, product = str(demand.get("id", "")), str(demand.get("item", ""))
        quantity = _number(demand.get("quantity", 0), "demand quantity")
        if not demand_id or not product:
            raise ValueError("demands require id and item")
        due = demand.get("due_at", demand.get("due_date", demand.get("due")))
        revenue = _number(demand.get("unit_revenue", demand.get("revenue", 0)), "unit_revenue")
        if "unit_revenue" in demand:
            revenue *= quantity
        resource = str(demand.get("resource", "default"))
        prepared_demands.append({
            "id": demand_id, "item": product, "quantity": quantity,
            "due": _at(due, anchor, "demand due date") if due is not None else anchor + timedelta(days=9999),
            "priority": float(demand.get("priority", 0)), "revenue": revenue,
            "duration": timedelta(days=_number(demand.get("processing_days", 1), "processing_days")),
            "resources": [resource] + [str(value) for value in demand.get("alternate_resources", []) if str(value) != resource],
            "requirements": explode(product, quantity),
        })
    return {"anchor": anchor, "demands": prepared_demands, "stock": stock, "reservations": reservations,
            "commitments": commitments, "options": options, "fixed_tasks": fixed_tasks}


def _simulate(prepared: dict, policy: dict | None = None) -> dict:
    """Run one deterministic allocation + schedule under a candidate policy."""
    policy = policy or {}
    anchor = prepared["anchor"]
    expedite, buy, qualify = set(policy.get("expedite", ())), set(policy.get("buy", ())), set(policy.get("qualify", ()))
    released = set(policy.get("release_reservations_for", ()))
    replan = bool(policy.get("replan"))

    stock = [dict(row, remaining=row["quantity"]) for row in prepared["stock"]]
    reserved_for: dict[tuple[str, str], list[dict]] = defaultdict(list)
    for reservation in prepared["reservations"]:
        if reservation["item"] in released:
            continue
        remaining = reservation["quantity"]
        for lot in (row for row in stock if row["item"] == reservation["item"]):
            take = min(remaining, lot["remaining"])
            if take > EPSILON:
                lot["remaining"] -= take
                remaining -= take
                reserved_for[(reservation["item"], reservation["demand_id"])].append(
                    {"source_id": lot["id"], "location": lot["location"], "remaining": take, "reservation_id": reservation["id"]})

    sources: dict[str, list[dict]] = defaultdict(list)
    for row in prepared["commitments"]:
        available_at = row["available_at"]
        if row["id"] in expedite and row["expedite_available_at"] is not None:
            available_at = min(available_at, row["expedite_available_at"])
        sources[row["item"]].append(dict(row, remaining=row["quantity"], available_at=available_at))
    for row in prepared["options"]:
        if row["id"] in buy or row["id"] in qualify:
            sources[row["item"]].append(dict(row, remaining=row["quantity"]))
    for rows in sources.values():
        rows.sort(key=lambda row: (row["available_at"], row["unit_cost"], row["id"]))

    def item_need(demand: dict, item: str) -> float:
        return sum(row["quantity"] for row in demand["requirements"] if row["item"] == item)

    density_item = policy.get("reallocate_item")

    def order_key(demand: dict) -> tuple:
        base = (demand["due"], -demand["priority"], demand["id"])
        if not density_item:
            return base
        need = item_need(demand, density_item)
        return (-(demand["revenue"] / need) if need > EPSILON else -inf,) + base

    busy: dict[str, list[tuple[datetime, datetime]]] = defaultdict(list)
    for task in prepared["fixed_tasks"]:
        busy[task["resource"]].append((task["start"], task["finish"]))

    impacts, shortages, allocations, schedule = [], [], [], []
    for demand in sorted(prepared["demands"], key=order_key):
        mutable = stock + [row for rows in sources.values() for row in rows] + \
                  [row for key, rows in reserved_for.items() if key[1] == demand["id"] for row in rows]
        checkpoint = [(row, row["remaining"]) for row in mutable]
        order_shortages, used, ready_at = [], [], anchor
        for requirement in demand["requirements"]:
            material, remaining = requirement["item"], requirement["quantity"]
            pools = [("reserved_inventory", row) for row in reserved_for.get((material, demand["id"]), [])] + \
                    [("inventory", row) for row in stock if row["item"] == material] + \
                    [(row["kind"], row) for row in sources.get(material, [])]
            for kind, pool in pools:
                if remaining <= EPSILON:
                    break
                take = min(remaining, pool["remaining"])
                if take <= EPSILON:
                    continue
                pool["remaining"] -= take
                remaining -= take
                available_at = pool.get("available_at", anchor)
                ready_at = max(ready_at, available_at)
                entry = {"item": material, "source": kind, "source_id": pool.get("source_id", pool.get("id")),
                         "quantity": round(take, 6), "available_at": available_at.isoformat(), "path": requirement["path"]}
                if kind in {"reserved_inventory", "inventory"}:
                    entry["location"] = pool["location"]
                else:
                    entry.update(supplier=pool["supplier"], unit_cost=pool["unit_cost"])
                    if kind == "purchase_option":
                        entry["qualified"] = pool["qualified"]
                used.append(entry)
            if remaining > EPSILON:
                order_shortages.append({"demand_id": demand["id"], "item": material, "quantity": round(remaining, 6), "path": requirement["path"]})

        due = demand["due"]
        if order_shortages:
            for row, value in checkpoint:
                row["remaining"] = value
            shortages.extend(order_shortages)
            impacts.append({"demand_id": demand["id"], "item": demand["item"], "quantity": demand["quantity"],
                            "due_at": due.isoformat(), "finish_at": None, "late_days": 0, "status": "blocked",
                            "revenue": round(demand["revenue"], 6), "revenue_at_risk": round(demand["revenue"], 6),
                            "resource": None, "shortages": order_shortages, "used_sources": [],
                            "root_cause_items": sorted({row["item"] for row in order_shortages}),
                            "reason_codes": ["material_shortage"]})
            continue

        resources = demand["resources"] if replan else demand["resources"][:1]
        options = []
        for rank, resource in enumerate(resources):
            start = _earliest_slot(busy[resource], ready_at, demand["duration"])
            options.append((start + demand["duration"], rank, resource, start))
        finish, _, resource, start = min(options)
        busy[resource].append((start, finish))
        late_days = int(ceil(max(0.0, (finish - due).total_seconds() / 86400) - EPSILON))
        reasons, root_items = [], []
        if late_days:
            if ready_at + demand["duration"] > due:
                reasons.append("late_material")
                root_items = sorted({row["item"] for row in used if row["available_at"] == ready_at.isoformat()})
            else:
                reasons.append("capacity_conflict")
        at_risk = demand["revenue"] if late_days else 0.0
        for row in used:
            allocations.append({"demand_id": demand["id"], "item": row["item"], "source_id": row["source_id"], "quantity": row["quantity"]})
        schedule.append({"demand_id": demand["id"], "resource": resource, "start": start.isoformat(), "finish": finish.isoformat(),
                         "reassigned_from": demand["resources"][0] if resource != demand["resources"][0] else None})
        impacts.append({"demand_id": demand["id"], "item": demand["item"], "quantity": demand["quantity"],
                        "due_at": due.isoformat(), "finish_at": finish.isoformat(), "late_days": late_days,
                        "status": "late" if late_days else "on_time", "revenue": round(demand["revenue"], 6),
                        "revenue_at_risk": round(at_risk, 6), "resource": resource, "shortages": [], "used_sources": used,
                        "root_cause_items": root_items, "reason_codes": reasons})

    impacts.sort(key=lambda row: row["demand_id"])
    stranded = [{"item": key[0], "demand_id": key[1], "source_id": row["source_id"], "quantity": round(row["remaining"], 6)}
                for key, rows in sorted(reserved_for.items()) for row in rows if row["remaining"] > EPSILON
                and any(impact["demand_id"] == key[1] and impact["status"] == "blocked" for impact in impacts)]
    purchase_cost = sum(row["quantity"] * row["unit_cost"] for impact in impacts for row in impact["used_sources"] if row["source"] == "purchase_option")
    expedite_cost = sum(row["quantity"] * row["expedite_unit_premium"] for row in prepared["commitments"]
                        if row["id"] in expedite and row["expedite_available_at"] is not None)
    return {"impacts": impacts, "shortages": shortages, "allocations": allocations, "schedule": schedule, "stranded": stranded,
            "revenue_at_risk": round(sum(row["revenue_at_risk"] for row in impacts), 6),
            "incremental_cost": round(purchase_cost + expedite_cost, 6)}


def _impact_path(result: dict) -> dict:
    nodes: dict[str, dict] = {}
    edges: dict[tuple, dict] = {}
    for impact in result["impacts"]:
        demand_node = f"demand:{impact['demand_id']}"
        nodes[demand_node] = {"id": demand_node, "kind": "CustomerOrder", "label": impact["demand_id"],
                              "attributes": {"item": impact["item"], "quantity": impact["quantity"], "due_at": impact["due_at"], "status": impact["status"]}}
        paths = [row["path"] for row in impact["shortages"]] or \
                [row["path"] for row in impact["used_sources"] if row["item"] in impact["root_cause_items"]]
        for path in paths:
            previous = demand_node
            for depth, path_item in enumerate(path):
                node_id = f"material:{path_item}" if depth == len(path) - 1 else f"bom:{impact['demand_id']}:{depth}:{path_item}"
                nodes.setdefault(node_id, {"id": node_id, "kind": "Item", "label": path_item, "attributes": {}})
                edges.setdefault((previous, node_id, "REQUIRES"), {"source": previous, "target": node_id, "kind": "REQUIRES"})
                previous = node_id
        for row in impact["shortages"]:
            key = (f"material:{row['item']}", demand_node, "BLOCKS")
            edges[key] = {"source": key[0], "target": demand_node, "kind": "BLOCKS", "quantity_delta": row["quantity"],
                          "evidence_refs": [f"bom-path:{'/'.join(row['path'])}"]}
        if "late_material" in impact["reason_codes"]:
            for row in impact["used_sources"]:
                if row["item"] not in impact["root_cause_items"]:
                    continue
                source_node = f"supply:{row['source_id']}"
                nodes.setdefault(source_node, {"id": source_node, "kind": "SupplyCommitment" if row["source"] == "commitment" else "SupplySource",
                                               "label": row["source_id"], "attributes": {"supplier": row.get("supplier"), "available_at": row["available_at"]}})
                edges.setdefault((source_node, f"material:{row['item']}", "SUPPLIES"),
                                 {"source": source_node, "target": f"material:{row['item']}", "kind": "SUPPLIES", "quantity_delta": row["quantity"]})
            key = tuple([f"material:{item}" for item in impact["root_cause_items"]][:1]) + (demand_node, "DELAYS")
            if len(key) == 3:
                edges[key] = {"source": key[0], "target": demand_node, "kind": "DELAYS",
                              "time_delta_seconds": impact["late_days"] * 86400, "evidence_refs": [f"schedule:{impact['demand_id']}"]}
    path = {"schema": "ImpactPathArtifact/v1",
            "roots": [f"demand:{row['demand_id']}" for row in result["impacts"] if row["revenue_at_risk"] > 0],
            "nodes": sorted(nodes.values(), key=lambda row: row["id"]),
            "edges": sorted(edges.values(), key=lambda row: (row["source"], row["target"], row["kind"])),
            "terminal_impacts": [{"demand_id": row["demand_id"], "revenue_at_risk": row["revenue_at_risk"], "late_days": row["late_days"],
                                  "status": row["status"]} for row in result["impacts"]],
            "completeness": "exact"}
    path["content_digest"] = _stable_digest(path)
    return path


def _candidate_specs(prepared: dict, baseline: dict) -> list[dict]:
    problem_items = sorted({item for row in baseline["impacts"] for item in row["root_cause_items"]})
    specs: list[dict] = []
    applicable_expedite, applicable_buy = [], []
    for item in problem_items:
        for row in prepared["commitments"]:
            if row["item"] == item and row["expedite_available_at"] is not None and row["expedite_available_at"] < row["available_at"]:
                applicable_expedite.append(row["id"])
                specs.append({"id": f"expedite:{row['id']}", "kind": "expedite", "item": item, "supplier": row["supplier"],
                              "label": f"加急 {row['id']}", "policy": {"expedite": [row["id"]]},
                              "actions": [{"action_type": "expedite_supply_commitment", "commitment_id": row["id"],
                                           "available_at": row["expedite_available_at"].isoformat()}]})
        for row in prepared["options"]:
            if row["item"] != item:
                continue
            if row["qualified"]:
                applicable_buy.append(row["id"])
                specs.append({"id": f"buy_alternate:{row['id']}", "kind": "buy_alternate", "item": item, "supplier": row["supplier"],
                              "label": f"向 {row['supplier']} 采购替代料", "policy": {"buy": [row["id"]]},
                              "actions": [{"action_type": "create_purchase_order", "option_id": row["id"], "supplier": row["supplier"], "item": item}]})
            else:
                specs.append({"id": f"qualify_alternate:{row['id']}", "kind": "qualify_alternate", "item": item, "supplier": row["supplier"],
                              "label": f"认证 {row['supplier']} 后采购", "policy": {"qualify": [row["id"]]},
                              "hard_constraints": [{"code": "supplier_not_qualified", "subject": f"SupplySource:{row['id']}"}],
                              "actions": [{"action_type": "qualify_supplier", "option_id": row["id"], "supplier": row["supplier"], "item": item}]})
        specs.append({"id": f"reallocate:{item}", "kind": "reallocate", "item": item, "label": f"按收入密度重新分配 {item}",
                      "policy": {"release_reservations_for": [item], "reallocate_item": item},
                      "actions": [{"action_type": "reprioritize_allocation", "item": item, "policy": "revenue_per_unit_desc",
                                   "released_reservations": [row["id"] for row in prepared["reservations"] if row["item"] == item]}]})
    has_alternate_resources = any(len(row["resources"]) > 1 for row in prepared["demands"])
    if (applicable_expedite or applicable_buy) and has_alternate_resources:
        specs.append({"id": "combined:expedite+alternate+replan", "kind": "combined", "item": ",".join(problem_items),
                      "label": "加急 + 合格替代 + 跨产线改排",
                      "policy": {"expedite": applicable_expedite, "buy": applicable_buy, "replan": True},
                      "actions": [action for spec in specs if spec["kind"] in {"expedite", "buy_alternate"} for action in spec["actions"]]
                                 + [{"action_type": "reschedule_work_orders", "policy": "earliest_finish_qualified_resource"}]})
    return specs[:MAX_CANDIDATES]


def _evaluate(prepared: dict, baseline: dict, spec: dict) -> dict:
    result = _simulate(prepared, spec["policy"])
    before = {row["demand_id"]: row for row in baseline["impacts"]}
    protected, harmed = [], []
    for row in result["impacts"]:
        old = before[row["demand_id"]]
        if old["revenue_at_risk"] > 0 and row["revenue_at_risk"] == 0:
            protected.append({"demand_id": row["demand_id"], "revenue": row["revenue"]})
        elif old["revenue_at_risk"] == 0 and row["revenue_at_risk"] > 0:
            harmed.append({"demand_id": row["demand_id"], "revenue": row["revenue"], "status": row["status"], "late_days": row["late_days"]})
    actions = [dict(row) for row in spec["actions"]]
    for row in actions:
        if row["action_type"] == "create_purchase_order":
            row["quantity"] = round(sum(a["quantity"] for a in result["allocations"] if a["source_id"] == row["option_id"]), 6)
    if spec["policy"].get("replan"):
        actions.extend({"action_type": "reassign_work_order", "demand_id": row["demand_id"], "from_resource": row["reassigned_from"],
                        "to_resource": row["resource"]} for row in result["schedule"] if row["reassigned_from"])
    hard = spec.get("hard_constraints", [])
    candidate = {"id": spec["id"], "kind": spec["kind"], "item": spec["item"], "label": spec["label"],
                 "requires_approval": True, "applicable": not hard, "hard_constraints": hard,
                 "revenue_at_risk": result["revenue_at_risk"],
                 "revenue_protected": round(baseline["revenue_at_risk"] - result["revenue_at_risk"], 6),
                 "protected_orders": protected, "harmed_orders": harmed, "incremental_cost": result["incremental_cost"],
                 "at_risk_orders": sorted(row["demand_id"] for row in result["impacts"] if row["revenue_at_risk"] > 0),
                 "actions": actions, "schedule": result["schedule"]}
    if spec.get("supplier"):
        candidate["supplier"] = spec["supplier"]
    if hard:
        candidate["constraint"] = hard[0]["code"]
    candidate["content_digest"] = _stable_digest(candidate)
    return candidate


def material_shortage_decision(inputs: dict) -> dict:
    """Allocate time-phased material, explain the impact and evaluate candidate actions.

    Order sequence: due date, descending priority, then order id.  Material
    sources per leaf: the order's own reservations, free stock (by location),
    then commitments by availability and cost.  Candidates change only the
    policy, never the algorithm.
    """
    prepared = _prepare(inputs)
    baseline = _simulate(prepared)
    candidates = [_evaluate(prepared, baseline, spec) for spec in _candidate_specs(prepared, baseline)]
    candidates.sort(key=lambda row: (not row["applicable"], -row["revenue_protected"], row["incremental_cost"], row["id"]))
    impacts = baseline["impacts"]
    kpis = {"revenue_at_risk": baseline["revenue_at_risk"],
            "at_risk_orders": sum(1 for row in impacts if row["revenue_at_risk"] > 0),
            "blocked_orders": sum(1 for row in impacts if row["status"] == "blocked"),
            "late_orders": sum(1 for row in impacts if row["status"] == "late"),
            "on_time_units": round(sum(row["quantity"] for row in impacts if row["status"] == "on_time"), 6),
            "demanded_units": round(sum(row["quantity"] for row in impacts), 6),
            "root_cause_items": sorted({item for row in impacts for item in row["root_cause_items"]}),
            "stranded_reserved_quantity": round(sum(row["quantity"] for row in baseline["stranded"]), 6)}
    return {"feasible": not baseline["shortages"], "shortages": baseline["shortages"], "demand_impacts": impacts,
            "allocations": baseline["allocations"], "schedule": baseline["schedule"], "candidate_actions": candidates,
            "revenue_at_risk": baseline["revenue_at_risk"], "kpis": kpis, "stranded_inventory": baseline["stranded"],
            "impact_path": _impact_path(baseline), "completeness": "exact"}
