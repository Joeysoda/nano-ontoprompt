"""MRP planning over a product portfolio: policies, coverage, revenue and actions.

Built on the material-shortage core (`_prepare`/`_simulate`) so allocation,
all-or-nothing orders, reservations, commitments and line scheduling are the
same code that has hand-checked known answers.  This module adds:

* planning policies per product line (capacity, pull-in window, conversion
  cost, risk-averse supply) and the quarter revenue / cost / OTIF they yield;
* time-phased MRP coverage (projected balance per part per week) and the
  planned orders lot-for-lot netting would create;
* candidate actions -- expedite late POs, rework from surplus parts, defer
  non-critical POs that would only arrive to wait, switch a line's policy --
  each evaluated by re-running the same simulation.
"""
from __future__ import annotations

from collections import defaultdict
from copy import deepcopy
from datetime import timedelta
from math import ceil, floor

from .material_shortage import EPSILON, _prepare, _simulate, _stable_digest, _time

MAX_DEFER_WEEKS = 8


def _rows(value):
    """Plain rows or Object Query records."""
    out = []
    for row in value or []:
        if isinstance(row, dict) and "object_id" in row and isinstance(row.get("properties"), dict):
            row = {**{k: v for k, v in row["properties"].items() if v is not None}, "id": row["object_id"]}
        out.append(row)
    return out


class Plan:
    def __init__(self, inputs: dict):
        self.raw = {key: (_rows(value) if isinstance(value, list) else value) for key, value in inputs.items()}
        self.anchor = _time(self.raw["scenario_time"], "scenario_time")
        self.weeks = int(self.raw.get("horizon_weeks") or 52)
        self.q_start, self.q_end = float(self.raw["quarter_start"]), float(self.raw["quarter_end"])
        # Master data is keyed by business code; object ids are namespaced per ontology.
        key = lambda row: row.get("code") or row["id"]
        self.lines = {key(row): {**row, "object_id": row["id"]} for row in self.raw["product_lines"]}
        self.policies = {key(row): {**row, "id": key(row)} for row in self.raw["policy_definitions"]}
        self.items = {key(row): row for row in self.raw.get("items", [])}
        self.demand_line = {row["id"]: row.get("product_line") for row in self.raw["demands"]}
        self.demand_rows = {row["id"]: row for row in self.raw["demands"]}
        self.bom = defaultdict(list)
        for row in self.raw["boms"]:
            self.bom[row["parent_item"]].append((row["component_item"], float(row["quantity_per"])))
        self.leaves = {}
        for demand in self.raw["demands"]:
            self.leaves.setdefault(demand["item"], dict(self._explode(demand["item"], 1.0)))
        self.material_cost = {top: sum(qty * float(self.items.get(part, {}).get("unit_cost", 0)) for part, qty in parts.items())
                              for top, parts in self.leaves.items()}
        # Which product lines use a part, for coverage filtering and attribution.
        self.part_lines = defaultdict(set)
        for demand in self.raw["demands"]:
            for part in self.leaves[demand["item"]]:
                self.part_lines[part].add(demand.get("product_line"))

    def _explode(self, item, qty):
        if item not in self.bom:
            yield item, qty
            return
        merged = defaultdict(float)
        for child, per in self.bom[item]:
            for leaf, amount in self._explode(child, qty * per):
                merged[leaf] += amount
        yield from merged.items()

    def policy_of(self, line, overrides):
        return self.policies[overrides.get(line) or self.lines[line]["policy"]]

    def model_inputs(self, overrides=None, defer=None):
        """Material-shortage inputs with each line's policy applied."""
        overrides, defer = overrides or {}, defer or {}
        raw = deepcopy(self.raw)
        for demand in raw["demands"]:
            policy = self.policy_of(demand["product_line"], overrides)
            demand["processing_days"] = float(demand["processing_days"]) / float(policy["capacity_factor"])
        risk_averse = {line for line in self.lines if self.policy_of(line, overrides).get("exclude_low_confidence")}
        commitments = []
        for row in raw["supply_commitments"]:
            owner = self.items.get(row["item"], {}).get("product_line")
            if owner in risk_averse and float(row.get("confidence", 1)) < 0.8:
                continue  # a risk-averse plan does not count on shaky supply
            if row["id"] in defer:
                row = {**row, "available_at": defer[row["id"]]}
            commitments.append(row)
        raw["supply_commitments"] = commitments
        return {key: raw[key] for key in ("scenario_time", "demands", "boms", "inventory", "reservations",
                                          "supply_commitments", "supply_options", "fixed_tasks")}

    def day(self, iso):
        return (_time(iso, "time") - self.anchor).total_seconds() / 86400

    def evaluate(self, overrides=None, sim_policy=None, defer=None):
        prepared = _prepare(self.model_inputs(overrides, defer))
        result = _simulate(prepared, sim_policy or {})
        overrides = overrides or {}
        events = []
        for impact in result["impacts"]:
            line = self.demand_line[impact["demand_id"]]
            policy = self.policy_of(line, overrides)
            due = self.day(impact["due_at"])
            ship = None
            if impact["status"] != "blocked":
                finish = self.day(impact["finish_at"])
                ship = finish if impact["status"] == "late" else max(finish, due - float(policy["pull_in_days"]))
            unit_cost = self.material_cost[impact["item"]] + float(self.lines[line]["conversion_cost"]) * float(policy["conversion_factor"])
            events.append({**impact, "product_line": line, "policy": policy["id"], "due_day": due, "ship_day": ship,
                           "customer": self.demand_rows[impact["demand_id"]].get("customer"),
                           "delay_days": 0 if ship is None or ship <= due else int(ceil(ship - due - EPSILON)),
                           "cost": round(impact["quantity"] * unit_cost, 2),
                           "in_quarter": ship is not None and self.q_start <= ship < self.q_end})
        return result, events

    def portfolio(self, events, overrides=None, planned_orders=None):
        overrides = overrides or {}
        rows = []
        for line_id, line in self.lines.items():
            mine = [row for row in events if row["product_line"] == line_id]
            due_q = [row for row in mine if self.q_start <= row["due_day"] < self.q_end]
            on_time = [row for row in mine if row["status"] == "on_time"]
            rows.append({"id": line_id, "name": line["name"], "policy": self.policy_of(line_id, overrides)["id"],
                         "projected_revenue": round(sum(row["revenue"] for row in mine if row["in_quarter"]), 2),
                         "planned_revenue": round(sum(row["revenue"] for row in due_q), 2),
                         "cost": round(sum(row["cost"] for row in mine if row["in_quarter"]), 2),
                         "otif": round(len(on_time) / len(mine), 4) if mine else 1.0,
                         "demand_events": len(mine), "at_risk": sum(1 for row in mine if row["status"] != "on_time"),
                         "planned_orders": (planned_orders or {}).get(line_id, 0)})
        return rows

    def coverage(self, result, sim_policy=None, defer=None):
        """Projected balance per part per week (lot-for-lot MRP, no planned orders applied)."""
        sim_policy, defer = sim_policy or {}, defer or {}
        expedite = set(sim_policy.get("expedite", ()))
        bucket = lambda day: min(self.weeks - 1, max(0, int(floor(day / 7))))
        # MRP nets on the date parts are REQUIRED (due date minus build time), not on
        # the scheduled start: a delay pushes the start back and would hide the deficit.
        gross = defaultdict(lambda: [0.0] * self.weeks)
        for demand in self.raw["demands"]:
            need_day = float(demand["due_at"]) - float(demand["processing_days"])
            for part, per in self.leaves[demand["item"]].items():
                gross[part][bucket(need_day)] += per * float(demand["quantity"])
        receipts = defaultdict(lambda: [0.0] * self.weeks)
        for row in self.raw["supply_commitments"]:
            at = float(defer.get(row["id"], row["available_at"]))
            if row["id"] in expedite and row.get("expedite_available_at") is not None:
                at = min(at, float(row["expedite_available_at"]))
            receipts[row["item"]][bucket(at)] += float(row["quantity"])
        onhand = defaultdict(float)
        for row in self.raw["inventory"]:
            onhand[row["item"]] += float(row["quantity"])
        rows, planned = [], defaultdict(int)
        for part in sorted(gross):
            balance, series, first_short, need_more = onhand[part], [], None, 0.0
            lead_weeks = int(ceil(float(self.items.get(part, {}).get("lead_time_days", 14)) / 7))
            for week in range(self.weeks):
                balance += receipts[part][week] - gross[part][week]
                series.append(round(balance, 2))
                if balance < -EPSILON and first_short is None:
                    first_short = week
            # Lot-for-lot: one planned order per week the running balance dips further.
            running, orders = onhand[part], []
            for week in range(self.weeks):
                running += receipts[part][week] - gross[part][week]
                if running < -EPSILON:
                    orders.append({"week": week, "release_week": week - lead_weeks, "quantity": round(-running, 2)})
                    need_more += -running
                    running = 0.0
            for line in self.part_lines[part]:
                planned[line] += len(orders)
            item = self.items.get(part, {})
            rows.append({"item": part, "name": item.get("name", part.split(":")[-1]), "lines": sorted(l for l in self.part_lines[part] if l),
                         "critical": bool(item.get("critical")), "on_hand": round(onhand[part], 2), "projected": series,
                         "first_shortage_week": first_short, "planned_orders": orders,
                         "past_due_releases": sum(1 for order in orders if order["release_week"] < 0)})
        weeks = [(self.anchor + timedelta(weeks=week)).date().isoformat() for week in range(self.weeks)]
        return {"weeks": weeks, "rows": rows}, dict(planned)


def _demand_by_month(plan, events):
    months = defaultdict(lambda: {"planned": 0.0, "at_risk": 0.0})
    for row in events:
        month = (plan.anchor + timedelta(days=row["due_day"])).strftime("%Y-%m")
        months[month]["at_risk" if row["status"] != "on_time" else "planned"] += row["quantity"]
    return [{"month": month, **values} for month, values in sorted(months.items())]


def _defer_moves(plan, coverage, events, line):
    """Non-critical POs of a line whose builds are blocked/late on a critical part.

    A PO is deferred to the week its line can actually build again (the
    earliest finish among that line's late/blocked orders, or horizon end),
    but only as far as the part's projected balance stays non-negative.
    """
    stuck = [row for row in events if row["product_line"] == line and row["status"] != "on_time"
             and any(plan.items.get(part, {}).get("critical") for part in row["root_cause_items"])]
    if not stuck:
        return {}
    resume = min((row["ship_day"] for row in stuck if row["ship_day"] is not None), default=plan.weeks * 7)
    by_item = {row["item"]: row for row in coverage["rows"]}
    moves = {}
    for po in plan.raw["supply_commitments"]:
        item = plan.items.get(po["item"], {})
        if item.get("product_line") != line or item.get("critical") or po["item"] not in by_item:
            continue
        arrive = float(po["available_at"])
        if arrive >= resume:
            continue
        series = by_item[po["item"]]["projected"]
        start, end = int(arrive // 7), min(len(series) - 1, int(min(resume, arrive + MAX_DEFER_WEEKS * 7) // 7))
        # Without this PO the balance must not go negative before its new date.
        target = start
        for week in range(start, end):
            if series[week] - float(po["quantity"]) < -EPSILON:
                break
            target = week + 1
        if target > start:
            moves[po["id"]] = target * 7
            for week in range(start, target):
                series[week] -= float(po["quantity"])
    return moves


def mrpspeed_planning(inputs: dict) -> dict:
    plan = Plan(inputs)
    base_result, base_events = plan.evaluate()
    coverage, planned = plan.coverage(base_result)
    base_portfolio = plan.portfolio(base_events, planned_orders=planned)
    target = round(sum(row["planned_revenue"] for row in base_portfolio), 2)

    def totals(portfolio):
        return {"projected_revenue": round(sum(row["projected_revenue"] for row in portfolio), 2),
                "cost": round(sum(row["cost"] for row in portfolio), 2),
                "otif": round(sum(row["otif"] * row["demand_events"] for row in portfolio) / max(1, sum(row["demand_events"] for row in portfolio)), 4),
                "demand_events": sum(row["demand_events"] for row in portfolio),
                "at_risk": sum(row["at_risk"] for row in portfolio)}

    base_totals = totals(base_portfolio)
    by_id = {row["demand_id"]: row for row in base_events}
    candidates = []

    def candidate(cid, kind, label, line, actions, *, overrides=None, sim_policy=None, defer=None, extra=None):
        result, events = plan.evaluate(overrides, sim_policy, defer)
        portfolio = plan.portfolio(events, overrides)
        after = totals(portfolio)
        protected = [{"demand_id": row["demand_id"], "revenue": row["revenue"]} for row in events
                     if by_id[row["demand_id"]]["status"] != "on_time" and row["status"] == "on_time"]
        harmed = [{"demand_id": row["demand_id"], "revenue": row["revenue"], "status": row["status"], "late_days": row["late_days"]}
                  for row in events if by_id[row["demand_id"]]["status"] == "on_time" and row["status"] != "on_time"]
        row = {"id": cid, "kind": kind, "label": label, "product_line": line, "item": line, "requires_approval": True,
               "applicable": True, "hard_constraints": [], "actions": actions,
               "revenue_at_risk": result["revenue_at_risk"], "revenue_protected": round(base_result["revenue_at_risk"] - result["revenue_at_risk"], 6),
               "protected_orders": protected, "harmed_orders": harmed, "incremental_cost": result["incremental_cost"],
               "quarter_revenue_after": after["projected_revenue"], "quarter_revenue_delta": round(after["projected_revenue"] - base_totals["projected_revenue"], 2),
               "quarter_cost_delta": round(after["cost"] - base_totals["cost"], 2), "otif_after": after["otif"],
               "at_risk_after": after["at_risk"], **(extra or {})}
        row["content_digest"] = _stable_digest(row)
        candidates.append(row)
        return row

    lines_at_risk = [row["id"] for row in base_portfolio if row["at_risk"]]
    critical = sorted({part for row in base_events for part in row["root_cause_items"] if plan.items.get(part, {}).get("critical")})
    # 1. Expedite every late, expeditable PO of a critical part.
    for part in critical:
        pos = [row for row in plan.raw["supply_commitments"] if row["item"] == part and row.get("expedite_available_at") is not None
               and float(row["expedite_available_at"]) < float(row["available_at"])]
        if pos:
            name = plan.items[part].get("name", part)
            candidate(f"expedite:{part}", "expedite", f"Expedite {len(pos)} POs for {name}", plan.items[part].get("product_line") or "shared",
                      [{"action_type": "expedite_supply_commitment", "commitment_id": po["id"], "available_at": po["expedite_available_at"]} for po in pos],
                      sim_policy={"expedite": [po["id"] for po in pos]}, extra={"item": part})
    # 2. Rework surplus parts into a critical part.
    for option in plan.raw["supply_options"]:
        if option["item"] in critical and option.get("consumes_item") and option.get("qualified", True):
            name = plan.items.get(option["item"], {}).get("name", option["item"])
            row = candidate(f"rework:{option['id']}", "rework", f"Expedite rework for {name}", plan.items.get(option["item"], {}).get("product_line") or "shared",
                            [{"action_type": "create_purchase_order", "option_id": option["id"], "supplier": option["supplier"], "item": option["item"]}],
                            sim_policy={"buy": [option["id"]]}, extra={"item": option["item"], "consumes_item": option["consumes_item"]})
            result, _ = plan.evaluate(None, {"buy": [option["id"]]})
            row["actions"][0]["quantity"] = round(sum(a["quantity"] for a in result["allocations"] if a["source_id"] == option["id"]), 6)
            if not row["actions"][0]["quantity"]:
                candidates.remove(row)
            row["content_digest"] = _stable_digest({k: v for k, v in row.items() if k != "content_digest"})
    # 3. Defer non-critical POs that would only arrive to wait (locked capital).
    for line in lines_at_risk:
        moves = _defer_moves(plan, deepcopy(coverage), base_events, line)
        if not moves:
            continue
        pos = {row["id"]: row for row in plan.raw["supply_commitments"]}
        deferred = sum(float(pos[po]["quantity"]) * float(pos[po]["unit_cost"]) for po in moves)
        row = candidate(f"defer:{line}", "defer_po", f"Reschedule {len(moves)} non-critical POs for {plan.lines[line]['name']}", line,
                        [{"action_type": "reschedule_supply_commitment", "commitment_id": po, "available_at": day,
                          "from": pos[po]["available_at"]} for po, day in sorted(moves.items())],
                        defer=moves, extra={"capital_deferred": round(deferred, 2), "pos_deferred": len(moves)})
        if row["harmed_orders"] or row["quarter_revenue_delta"] < 0:
            row["applicable"] = False
            row["hard_constraints"] = [{"code": "deferral_harms_delivery", "subject": line}]
    # 4. Fix every critical part of a line at once (one late part still blocks the build).
    singles = [row for row in candidates if row["kind"] in {"expedite", "rework"}]
    for line in sorted({row["product_line"] for row in singles}):
        mine = [row for row in singles if row["product_line"] == line]
        if len({row["item"] for row in mine}) < 2:
            continue
        expedite = [a["commitment_id"] for row in mine if row["kind"] == "expedite" for a in row["actions"]]
        buy = [a["option_id"] for row in mine if row["kind"] == "rework" for a in row["actions"]]
        candidate(f"combined:{line}", "combined", f"Expedite critical POs + rework for {plan.lines[line]['name']}", line,
                  [dict(a) for row in mine for a in row["actions"]], sim_policy={"expedite": expedite, "buy": buy})
    # 5. Switch a line's planning policy (best revenue gain per line).
    for line in plan.lines:
        best = None
        for policy in plan.policies:
            if policy == plan.lines[line]["policy"]:
                continue
            result, events = plan.evaluate({line: policy})
            gain = sum(row["revenue"] for row in events if row["in_quarter"]) - base_totals["projected_revenue"]
            if gain > EPSILON and (best is None or gain > best[1]):
                best = (policy, gain)
        if best:
            candidate(f"policy:{line}:{best[0]}", "set_policy", f"Set {plan.lines[line]['name']} to {best[0]}", line,
                      [{"action_type": "set_product_line_policy", "product_line": line, "policy": best[0], "from": plan.lines[line]["policy"]}],
                      overrides={line: best[0]})
    candidates.sort(key=lambda row: (not row["applicable"], -row["quarter_revenue_delta"], -row["revenue_protected"], row["id"]))

    day_iso = lambda day: (plan.anchor + timedelta(days=day)).date().isoformat()
    events = sorted(({"id": row["demand_id"], "product_line": row["product_line"], "item": row["item"], "customer": row["customer"],
                      "expected_ship_date": day_iso(row["due_day"]), "due_month": day_iso(row["due_day"])[:7],
                      "product_line_name": plan.lines[row["product_line"]]["name"], "at_risk": row["status"] != "on_time",
                      "root_cause_names": [plan.items.get(part, {}).get("name", part) for part in row["root_cause_items"]]
                                          or (["产能"] if "capacity_conflict" in row["reason_codes"] else []),
                      "quantity": row["quantity"], "due_day": row["due_day"], "ship_day": row["ship_day"], "status": row["status"],
                      "delay_days": row["delay_days"], "revenue": row["revenue"], "root_cause_items": row["root_cause_items"],
                      "reason_codes": row["reason_codes"], "policy": row["policy"]} for row in base_events),
                    key=lambda row: (row["due_day"], row["id"]))
    return {"scenario_time": plan.anchor.isoformat(), "quarter": {"start_day": plan.q_start, "end_day": plan.q_end, "revenue_target": target},
            "totals": {**base_totals, "revenue_target": target, "revenue_gap": round(base_totals["projected_revenue"] - target, 2),
                       "planned_orders": sum(len(row["planned_orders"]) for row in coverage["rows"])},
            "portfolio": base_portfolio, "policies": sorted(plan.policies), "demand_events": events,
            "demand_by_month": _demand_by_month(plan, base_events), "coverage": coverage, "candidate_actions": candidates,
            "projected_revenue": base_totals["projected_revenue"],
            "revenue_at_risk": base_result["revenue_at_risk"], "completeness": "exact"}
