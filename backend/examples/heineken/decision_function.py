"""Heineken-style container and destination decision, written as a user Function.

This file is user content: it is submitted through the Function API and runs
only in the isolated function runner.  The backend never imports it.

Inputs (Object Query records): warehouses, containers; plus scenario_day and
pickup_interval_days.  Output: {"decision": <decision/v1>}.

Model (deterministic, weekly buckets of 7 days over 3 weeks):
* A warehouse's week: available = carried stock + arrivals; unmet = max(0,
  demand - available); carried = available - demand - unmet (no backorders).
* Containers at port are collected one per ``pickup_interval_days``, in
  ``pickup_rank`` order (default: port arrival, then code); a collected
  container reaches its warehouse the next day; waiting costs demurrage.
* Containers at sea arrive on their ETA and cannot change destination.
  Containers loaded at origin can be re-routed before the booking cut-off to a
  destination the booking route allows; receiving capacity must hold.
"""
WEEKS = 3


def _props(records):
    return {row["object_id"]: {k: v for k, v in row["properties"].items() if v is not None} for row in records}


def simulate(warehouses, containers, day, interval, ranks=None, destinations=None):
    ranks, destinations = ranks or {}, destinations or {}
    arrivals = {code: [0.0] * WEEKS for code in (w["code"] for w in warehouses.values())}
    demurrage, next_slot = 0.0, day
    at_port = sorted((c for c in containers.values() if c["status"] == "at_port"),
                     key=lambda c: (ranks.get(c["code"], c.get("pickup_rank", 1e9)), c["port_day"], c["code"]))
    for container in at_port:
        pickup = max(next_slot, container["port_day"])
        next_slot = pickup + interval
        demurrage += (pickup - container["port_day"]) * container.get("demurrage_per_day", 0)
        week = int((pickup + 1 - day) // 7)
        if week < WEEKS:
            arrivals[destinations.get(container["code"], container["destination"])][week] += container["quantity"]
    for container in containers.values():
        if container["status"] in {"at_sea", "loaded_at_origin"}:
            week = int((container["eta_day"] - day) // 7)
            if 0 <= week < WEEKS:
                arrivals[destinations.get(container["code"], container["destination"])][week] += container["quantity"]
    result = {}
    for warehouse in warehouses.values():
        stock, unmet, overflow, weeks = warehouse["on_hand"], 0.0, [], []
        for week in range(WEEKS):
            available = stock + arrivals[warehouse["code"]][week]
            if available > warehouse["capacity"]:
                overflow.append(week + 1)
            demand = warehouse[f"demand_w{week + 1}"]
            short = max(0.0, demand - available)
            unmet += short
            stock = available - demand + short
            weeks.append({"week": week + 1, "arrivals": arrivals[warehouse["code"]][week], "unmet": short})
        result[warehouse["code"]] = {"unmet": unmet, "overflow_weeks": overflow, "weeks": weeks}
    return result, demurrage


def run(inputs):
    warehouses, containers = _props(inputs["warehouses"]), _props(inputs["containers"])
    day, interval = inputs["scenario_day"], inputs["pickup_interval_days"]
    ids = {c["code"]: object_id for object_id, c in containers.items()}
    wh_ids = {w["code"]: object_id for object_id, w in warehouses.items()}
    base, base_demurrage = simulate(warehouses, containers, day, interval)
    short = sorted(code for code, row in base.items() if row["unmet"] > 0)

    def urgency(code):  # first week with a shortfall at the container's destination
        weeks = base[containers[ids[code]]["destination"]]["weeks"]
        return next((row["week"] for row in weeks if row["unmet"] > 0), 99)

    port = sorted((c for c in containers.values() if c["status"] == "at_port"), key=lambda c: (urgency(c["code"]), c["port_day"], c["code"]))
    priority = {c["code"]: rank for rank, c in enumerate(port, start=1)}
    swaps = [(c, target) for c in sorted(containers.values(), key=lambda c: c["code"])
             if c["status"] == "loaded_at_origin" and c["cutoff_day"] > day for target in short if target != c["destination"]]

    def candidate(cid, label, ranks=None, destinations=None, actions=()):
        after, demurrage = simulate(warehouses, containers, day, interval, ranks, destinations)
        hard = [{"code": "capacity_exceeded", "subject": code, "weeks": row["overflow_weeks"]}
                for code, row in after.items() if row["overflow_weeks"] and not base[code]["overflow_weeks"]]
        for code, target in (destinations or {}).items():
            if target not in containers[ids[code]].get("allowed_destinations", []):
                hard.append({"code": "route_not_allowed", "subject": code, "destination": target})
        return {"id": cid, "label": label, "applicable": not hard, "hard_constraints": hard,
                "metric_after": sum(row["unmet"] for row in after.values()), "cost": demurrage - base_demurrage,
                "protected": [wh_ids[c] for c in after if base[c]["unmet"] > 0 and after[c]["unmet"] == 0],
                "harmed": [wh_ids[c] for c in after if base[c]["unmet"] == 0 and after[c]["unmet"] > 0],
                "actions": list(actions)}

    def rank_actions(ranks):
        return [{"action_type": "设置提箱顺序", "parameters": {"container": {"object_type": "Container", "object_id": ids[code]}, "rank": rank}}
                for code, rank in sorted(ranks.items(), key=lambda item: item[1])]

    def swap_action(code, target):
        return {"action_type": "改目的地", "parameters": {"container": {"object_type": "Container", "object_id": ids[code]}, "destination": target}}

    candidates = []
    default_order = [c["code"] for c in sorted((c for c in containers.values() if c["status"] == "at_port"), key=lambda c: (c["port_day"], c["code"]))]
    if [c["code"] for c in port] != default_order:
        candidates.append(candidate("prioritize_pickup", "按缺货紧迫度调整提箱顺序", ranks=priority, actions=rank_actions(priority)))
    for container, target in swaps:
        candidates.append(candidate(f"swap:{container['code']}:{target}", f"{container['code']} 改送 {target}",
                                    destinations={container["code"]: target}, actions=[swap_action(container["code"], target)]))
    usable = [(c, t) for c, t in swaps if t in c.get("allowed_destinations", [])]
    if usable and len(candidates) > len(swaps):
        moves = dict((c["code"], t) for c, t in usable)
        candidates.append(candidate("combined", "调整提箱顺序 + " + "、".join(f"{k} 改送 {v}" for k, v in moves.items()),
                                    ranks=priority, destinations=moves,
                                    actions=rank_actions(priority) + [swap_action(k, v) for k, v in moves.items()]))
    candidates.sort(key=lambda row: (not row["applicable"], row["metric_after"], row["cost"], row["id"]))

    subjects = []
    for code, row in sorted(base.items()):
        late = [w for w in row["weeks"] if w["unmet"] > 0]
        subjects.append({"id": wh_ids[code], "label": code, "status": "short" if row["unmet"] else "covered",
                         "at_risk": row["unmet"] > 0, "metric_value": row["unmet"],
                         "reasons": [f"第 {w['week']} 周缺 {w['unmet']:g} 箱" for w in late],
                         "evidence": [{"label": f"第 {w['week']} 周到货", "value": w["arrivals"]} for w in row["weeks"]]})
    decision = {"schema": "decision/v1", "title": "未来三周仓库供需与集装箱调整", "subject_type": "Warehouse", "subject_label": "仓库",
                "metric": {"key": "unmet_cases", "label": "三周缺货箱数", "unit": "箱", "direction": "minimize",
                           "baseline": sum(row["unmet"] for row in base.values())},
                "kpis": [{"label": "缺货仓库", "value": len(short)}, {"label": "当前滞港费", "value": base_demurrage},
                         {"label": "可改目的地集装箱", "value": len({c["code"] for c, _ in swaps})}],
                "subjects": subjects, "candidates": candidates}
    return {"decision": decision}
