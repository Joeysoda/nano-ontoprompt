"""Versioned, bounded supplier planning over a pinned frePPLe graph snapshot."""
from __future__ import annotations

from collections import defaultdict
from datetime import datetime, timedelta
from math import ceil, isfinite

WOOD = ("wooden beam", "wooden panel")
MODEL_VERSION = "2.1.0"
METRIC_CONTRACT = "supplier-metrics-v1"


def _number(value, field):
    if isinstance(value, bool):
        raise ValueError(f"invalid {field}")
    try:
        result = float(value)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid {field}") from exc
    if result < 0 or not isfinite(result):
        raise ValueError(f"invalid {field}")
    return result


def validate_profile(profile):
    if not isinstance(profile, dict) or not profile.get("supplier"):
        raise ValueError("supplier is required")
    allowed = {"supplier", "lead_days", "minimum_order", "order_multiple", "capacity", "cost_multiplier", "provenance"}
    if set(profile) - allowed:
        raise ValueError("unknown or protected supplier Action parameter")
    if not isinstance(profile["supplier"], str) or len(profile["supplier"]) > 100:
        raise ValueError("invalid supplier")
    for field in ("lead_days", "minimum_order", "order_multiple", "capacity"):
        if not isinstance(profile.get(field), dict) or set(profile[field]) != set(WOOD):
            raise ValueError(f"{field} must cover both wood materials")
        for item in WOOD:
            value = _number(profile[field][item], field)
            if field == "order_multiple" and value <= 0:
                raise ValueError("order_multiple must be positive")
    _number(profile.get("cost_multiplier"), "cost_multiplier")


def _rows(snapshot, concrete_type):
    return [(identity, props) for identity, props in snapshot.objects.items() if identity[0] == concrete_type]


def calculate(snapshot, profile, scenario_time, scope_items=WOOD):
    validate_profile(profile)
    scope = tuple(item for item in WOOD if item in scope_items)
    if not scope:
        raise ValueError("scope does not contain supported wood materials")
    anchor = datetime.fromisoformat(scenario_time.replace("Z", "+00:00"))
    demands = [(key, props) for key, props in _rows(snapshot, "Demand") if props.get("status") == "open"]
    if len(demands) != 16:
        raise ValueError(f"incomplete demand snapshot: expected 16, found {len(demands)}")
    items = {props.get("name"): (key, props) for key, props in _rows(snapshot, "Item")}
    if not set(scope) <= set(items):
        raise ValueError("wood item missing from snapshot")
    materials = [props for _, props in _rows(snapshot, "MaterialRequirement")]
    operations = defaultdict(lambda: {"inputs": [], "outputs": []})
    for props in materials:
        name = props.get("operation_id")
        quantity = float(props.get("quantity", 0))
        if quantity < 0:
            operations[name]["inputs"].append((props.get("item_id"), -quantity))
        elif quantity > 0:
            operations[name]["outputs"].append((props.get("item_id"), quantity))
    producers = defaultdict(list)
    for operation, parts in operations.items():
        for item, output in parts["outputs"]:
            producers[item].append((operation, output, parts["inputs"]))

    def wood_requirements(item, quantity, visited=frozenset()):
        if item in scope:
            return {item: quantity}
        if item in visited or not producers.get(item):
            return {}
        operation, output, inputs = sorted(producers[item], key=lambda row: row[0])[0]
        result = defaultdict(float)
        for source, factor in inputs:
            for wood, amount in wood_requirements(source, quantity * factor / output, visited | {item}).items():
                result[wood] += amount
        return dict(result)

    inventory = defaultdict(float)
    for _, props in _rows(snapshot, "Inventory"):
        if props.get("item_id") in scope:
            inventory[props["item_id"]] += _number(props.get("onhand", 0), "onhand")
    sorted_demands = sorted(demands, key=lambda row: (str(row[1].get("due", "")), int(row[1].get("priority", 0)), row[0][1]))
    gross = defaultdict(float)
    per_demand = []
    for key, demand in sorted_demands:
        quantity = _number(demand.get("quantity"), "demand quantity")
        required = wood_requirements(demand.get("item_id"), quantity)
        for item, amount in required.items():
            gross[item] += amount
        per_demand.append((key, demand, required))
    remaining_stock = dict(inventory)
    net = {item: max(0.0, gross[item] - inventory[item]) for item in scope}
    purchase = {}
    for item in scope:
        minimum = _number(profile["minimum_order"][item], "minimum_order")
        multiple = _number(profile["order_multiple"][item], "order_multiple")
        capacity = _number(profile["capacity"][item], "capacity")
        planned = ceil(max(net[item], minimum) / multiple) * multiple if net[item] else 0.0
        purchase[item] = min(planned, capacity)
    remaining_purchase = dict(purchase)
    impacts = []
    inventory_consumed = 0.0
    cost = sum(purchase[item] * _number(items[item][1].get("cost", 0), "item cost")
               * float(profile["cost_multiplier"]) for item in scope)
    lead_weight = sum(purchase[item] * float(profile["lead_days"][item]) for item in scope)
    for key, demand, required in per_demand:
        due = datetime.fromisoformat(str(demand["due"]))
        if anchor.tzinfo and not due.tzinfo:
            due = due.replace(tzinfo=anchor.tzinfo)
        shortages = {}
        used_purchase = {}
        late_days = 0
        for item, amount in required.items():
            stock = min(amount, remaining_stock.get(item, 0.0))
            remaining_stock[item] -= stock
            inventory_consumed += stock
            need = amount - stock
            bought = min(need, remaining_purchase.get(item, 0.0))
            remaining_purchase[item] -= bought
            used_purchase[item] = round(bought, 4)
            shortages[item] = round(max(0.0, need - bought), 4)
            arrival = anchor + timedelta(days=float(profile["lead_days"][item]))
            if bought and arrival > due:
                late_days = max(late_days, ceil((arrival - due).total_seconds() / 86400))
        if any(shortages.values()):
            late_days = max(late_days, 1)
        impacts.append({"object_id": key[1], "demand": demand.get("name"), "item": demand.get("item_id"),
                        "customer": demand.get("customer_id"), "due": due.isoformat(),
                        "quantity": float(demand["quantity"]), "required_wood": {k: round(v, 4) for k, v in required.items()},
                        "purchased_wood": used_purchase, "shortages": shortages, "late_days": late_days,
                        "promised_date": (due + timedelta(days=late_days)).isoformat(),
                        "reason_codes": (["supplier_capacity"] if any(shortages.values()) else []) + (["lead_time"] if late_days else [])})
    late = [item for item in impacts if item["late_days"]]
    quantity_short = sum(sum(row["shortages"].values()) for row in impacts)
    total_purchase = sum(purchase.values())
    metrics = [
        ("on_time_delivery_rate", "On-time delivery", round(100 * (len(impacts) - len(late)) / len(impacts), 2), "%"),
        ("late_demand_count", "Late demands", len(late), "demands"),
        ("shortage_quantity", "Wood shortage", round(quantity_short, 2), "source units"),
        ("estimated_procurement_cost", "Estimated procurement cost", round(cost, 2), "cost units"),
        ("weighted_lead_time_days", "Weighted lead time", round(lead_weight / total_purchase, 2) if total_purchase else 0, "days"),
        ("affected_customer_count", "Affected customers", len({row["customer"] for row in late}), "customers"),
        ("inventory_consumed", "Inventory consumed", round(inventory_consumed, 2), "source units"),
        ("purchase_quantity", "Purchase quantity", round(total_purchase, 2), "source units"),
    ]
    warnings = [{"code": "source_units_unspecified", "severity": "warning", "message": "Fixture wood units are source-defined; costs use synthetic cost units."}]
    if quantity_short:
        warnings.append({"code": "supplier_capacity", "severity": "warning", "message": f"{quantity_short:.2f} wood units exceed available supply."})
    return {"demand_impacts": impacts, "supply_plan": [{"item": item, "supplier": profile["supplier"],
            "gross_required": round(gross[item], 2), "inventory": round(inventory[item], 2), "purchase_quantity": purchase[item],
            "lead_days": profile["lead_days"][item], "minimum_order": profile["minimum_order"][item],
            "order_multiple": profile["order_multiple"][item], "capacity": profile["capacity"][item]} for item in scope],
            "metrics": [{"key": key, "label": label, "value": value, "unit": unit, "completeness": "exact"} for key, label, value, unit in metrics],
            "warnings": warnings, "completeness": "complete", "exact": True, "model_version": MODEL_VERSION,
            "metric_contract": METRIC_CONTRACT}
