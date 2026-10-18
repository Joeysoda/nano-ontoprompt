"""Deterministic local logic binding registry.

Each asset has the same contract: typed inputs, a versioned implementation,
structured output, and an auditable trace.  These are deliberately local
implementations so they can be tested without an LLM or external service.
"""
from __future__ import annotations
from itertools import product
from math import ceil, sqrt
from typing import Any
from .logic_contracts import normalize_contract, normalize_units, validate_payload

ASSET_DEFINITIONS = [
    {"asset_key":"release_gate_v1","name":"生产放行门","kind":"business_rule","implementation":"release_gate_v1","description":"根据点检、夹紧压力和加工完成状态判断是否放行。","input_schema":{"required":["tool_condition","visual_inspection","machining_finalized","clamp_pressure"]},"output_schema":{"fields":["decision","violations"]},"bindings":{"ontology_fields":["tool_condition","visual_inspection","machining_finalized","clamp_pressure"]}},
    {"asset_key":"cycle_time_v1","name":"周期时间计算","kind":"mathematical","implementation":"cycle_time_v1","description":"统一以秒计算批次加工周期。","input_schema":{"required":["setup_seconds","unit_seconds","quantity"]},"output_schema":{"fields":["total_seconds","total_minutes"]},"bindings":{"ontology_fields":["setup_seconds","unit_seconds","quantity"]}},
    {"asset_key":"uci_steel_energy_tariff_v1","name":"钢铁能耗电价敏感性","kind":"what_if","implementation":"uci_steel_energy_tariff_v1","description":"基于固定 UCI 钢铁能耗数据集的分类能耗汇总计算电价变化影响；物理能耗保持不变，电价为示例假设。","input_schema":{"required":["rates_per_kwh","maximum_load_rate_change"]},"output_schema":{"fields":["baseline_cost","candidate_cost","cost_delta","usage_kwh_before","usage_kwh_after","usage_delta_kwh","dataset_sha256","rates_are_illustrative"]},"bindings":{"dataset_id":851,"dataset_sha256":"9b1cee6f9cb9cd9df2b95814ca90a9a2ff15b7f5f1fba0fae3c643e82072eacc","ontology_fields":[]}},
    {"asset_key":"observation_summary_v1","name":"观测统计摘要","kind":"statistics","implementation":"observation_summary_v1","description":"对时序观测窗口计算均值、波动、趋势和异常比例。","input_schema":{"required":["values"]},"output_schema":{"fields":["count","mean","minimum","maximum","stdev","trend_slope","anomaly_rate"]},"bindings":{"ontology_fields":["observation.values"]}},
    {"asset_key":"tool_wear_risk_v1","name":"刀具磨损风险评分","kind":"machine_learning","implementation":"tool_wear_risk_v1","description":"版本化的可解释加权风险模型；输入来自观测特征，输出风险等级和贡献。","input_schema":{"required":["temperature","vibration","pressure"]},"output_schema":{"fields":["score","risk_level","contributions","model_version"]},"bindings":{"ontology_fields":["temperature","vibration","pressure"]}},
    {"asset_key":"resource_assignment_v1","name":"资源分配优化","kind":"optimization","implementation":"resource_assignment_v1","description":"在资格约束下最小化最大资源负载，返回可审计分配。","input_schema":{"required":["tasks","resources"]},"output_schema":{"fields":["assignments","objective","feasible"]},"bindings":{"ontology_fields":["operation","work_center","duration"]}},
    {"asset_key":"priority_plan_v1","name":"优先级生产计划","kind":"planning","implementation":"priority_plan_v1","description":"遵守前置依赖和资源不重叠，按优先级生成可执行计划。","input_schema":{"required":["tasks"]},"output_schema":{"fields":["schedule","makespan","feasible"]},"bindings":{"ontology_fields":["demand","operation","due_date","priority"]}},
    {"asset_key":"supplier_disruption_impact_v1","name":"供应商中断影响","kind":"what_if","implementation":"supplier_disruption_impact_v1","description":"Deterministic synthetic-demo impact calculation for a bounded supplier outage.","input_schema":{"required":["demands","supply_options"]},"output_schema":{"fields":["shortages","alternatives","feasible"]},"bindings":{"ontology_fields":["demand","supply_option","lead_time","quantity"]}},
    {"asset_key":"capacity_replan_v1","name":"需求激增产能重排","kind":"what_if","implementation":"capacity_replan_v1","description":"Deterministic feasible schedule; this is not a global optimum claim.","input_schema":{"required":["tasks","capacity"]},"output_schema":{"fields":["schedule","late_tasks","makespan","feasible"]},"bindings":{"ontology_fields":["demand","operation","resource","capacity"]}},
    {"asset_key":"supply_chain_reasoning_v1","name":"供应中断端到端推理","kind":"what_if","implementation":"supply_chain_reasoning_v1","description":"Bounded deterministic reasoning across multi-level BOM, qualified alternatives, material arrival, production capacity and order delivery.","input_schema":{"required":["demands","supply_options","boms","inventory","scenario_time"]},"output_schema":{"fields":["supply_plan","demand_impacts","schedule","shortages","alternatives","feasible","total_cost","late_order_count"]},"bindings":{"ontology_fields":["supplier","material","bom","inventory","work_order","work_center"]}},
    {"asset_key":"lot_recall_trace_v1","name":"质量批次召回追踪","kind":"what_if","implementation":"lot_recall_trace_v1","description":"Bounded, cycle-safe synthetic lot genealogy traversal.","input_schema":{"required":["start_lot","links"]},"output_schema":{"fields":["affected","paths","complete"]},"bindings":{"ontology_fields":["lot","consumption","production","shipment","customer"]}},
]

def _schema(asset_key: str, properties: dict, required: list[str], *, output: bool = False) -> dict:
    return {
        "$schema": "https://json-schema.org/draft/2020-12/schema",
        "$id": f"urn:logic:{asset_key}:{'output' if output else 'input'}:1.0.0",
        "type": "object", "properties": properties, "required": required,
        "additionalProperties": False,
    }

_CONTRACTS = {
    "release_gate_v1": (_schema("release_gate_v1", {"tool_condition":{"type":"string"},"visual_inspection":{"type":"string"},"machining_finalized":{"type":"boolean"},"clamp_pressure":{"type":"number","x-unit-code":"bar"}}, ["tool_condition","visual_inspection","machining_finalized","clamp_pressure"]), _schema("release_gate_v1", {"decision":{"type":"string","enum":["release","hold"]},"violations":{"type":"array","items":{"type":"string"}}}, ["decision","violations"], output=True)),
    "cycle_time_v1": (_schema("cycle_time_v1", {"setup_seconds":{"type":"number","minimum":0,"x-unit-code":"s"},"unit_seconds":{"type":"number","minimum":0,"x-unit-code":"s"},"quantity":{"type":"integer","minimum":0}}, ["setup_seconds","unit_seconds","quantity"]), _schema("cycle_time_v1", {"total_seconds":{"type":"number","x-unit-code":"s"},"total_minutes":{"type":"number","x-unit-code":"min"},"quantity":{"type":"integer"}}, ["total_seconds","total_minutes","quantity"], output=True)),
    "uci_steel_energy_tariff_v1": (_schema("uci_steel_energy_tariff_v1", {"rates_per_kwh":{"type":"object","properties":{"Light_Load":{"type":"number","minimum":0},"Medium_Load":{"type":"number","minimum":0},"Maximum_Load":{"type":"number","minimum":0}},"required":["Light_Load","Medium_Load","Maximum_Load"],"additionalProperties":False},"maximum_load_rate_change":{"type":"number","minimum":-1}}, ["rates_per_kwh","maximum_load_rate_change"]), _schema("uci_steel_energy_tariff_v1", {"baseline_cost":{"type":"number"},"candidate_cost":{"type":"number"},"cost_delta":{"type":"number"},"usage_kwh_before":{"type":"number"},"usage_kwh_after":{"type":"number"},"usage_delta_kwh":{"type":"number"},"dataset_sha256":{"type":"string"},"rates_are_illustrative":{"type":"boolean"}}, ["baseline_cost","candidate_cost","cost_delta","usage_kwh_before","usage_kwh_after","usage_delta_kwh","dataset_sha256","rates_are_illustrative"], output=True)),
    "observation_summary_v1": (_schema("observation_summary_v1", {"values":{"type":"array","minItems":1,"items":{"type":"number"}},"anomaly_threshold":{"type":"number","minimum":0}}, ["values"]), _schema("observation_summary_v1", {"count":{"type":"integer"},"mean":{"type":"number"},"minimum":{"type":"number"},"maximum":{"type":"number"},"stdev":{"type":"number"},"trend_slope":{"type":"number"},"anomaly_rate":{"type":"number","minimum":0,"maximum":1}}, ["count","mean","minimum","maximum","stdev","trend_slope","anomaly_rate"], output=True)),
    "tool_wear_risk_v1": (_schema("tool_wear_risk_v1", {"temperature":{"type":"number","x-unit-code":"Cel"},"vibration":{"type":"number","minimum":0,"x-unit-code":"m/s^2"},"pressure":{"type":"number","minimum":0,"x-unit-code":"bar"}}, ["temperature","vibration","pressure"]), _schema("tool_wear_risk_v1", {"score":{"type":"number","minimum":0,"maximum":1},"risk_level":{"type":"string","enum":["low","medium","high"]},"contributions":{"type":"object","additionalProperties":{"type":"number"}},"model_version":{"type":"string"}}, ["score","risk_level","contributions","model_version"], output=True)),
    "resource_assignment_v1": (_schema("resource_assignment_v1", {"tasks":{"type":"array","minItems":1},"resources":{"type":"array","minItems":1}}, ["tasks","resources"]), _schema("resource_assignment_v1", {"assignments":{"type":"array"},"objective":{"type":["number","null"]},"loads":{"type":"object"},"feasible":{"type":"boolean"},"reason":{"type":"string"}}, ["assignments","objective","feasible"], output=True)),
    "priority_plan_v1": (_schema("priority_plan_v1", {"tasks":{"type":"array","minItems":1}}, ["tasks"]), _schema("priority_plan_v1", {"schedule":{"type":"array"},"makespan":{"type":"number","minimum":0},"feasible":{"type":"boolean"}}, ["schedule","makespan","feasible"], output=True)),
    "supplier_disruption_impact_v1": (_schema("supplier_disruption_impact_v1", {"demands":{"type":"array"},"supply_options":{"type":"array"},"boms":{"type":"array"}}, ["demands","supply_options"]), _schema("supplier_disruption_impact_v1", {"shortages":{"type":"array"},"alternatives":{"type":"array"},"feasible":{"type":"boolean"}}, ["shortages","alternatives","feasible"], output=True)),
    "capacity_replan_v1": (_schema("capacity_replan_v1", {"tasks":{"type":"array"},"capacity":{"type":"number","minimum":0}}, ["tasks","capacity"]), _schema("capacity_replan_v1", {"schedule":{"type":"array"},"late_tasks":{"type":"array"},"makespan":{"type":"number"},"feasible":{"type":"boolean"}}, ["schedule","late_tasks","makespan","feasible"], output=True)),
    "supply_chain_reasoning_v1": (_schema("supply_chain_reasoning_v1", {"demands":{"type":"array","minItems":1},"supply_options":{"type":"array"},"boms":{"type":"array"},"inventory":{"type":"array"},"scenario_time":{"type":"string"}}, ["demands","supply_options","boms","inventory","scenario_time"]), _schema("supply_chain_reasoning_v1", {"supply_plan":{"type":"array"},"demand_impacts":{"type":"array"},"schedule":{"type":"array"},"shortages":{"type":"array"},"alternatives":{"type":"array"},"feasible":{"type":"boolean"},"total_cost":{"type":"number"},"late_order_count":{"type":"integer"}}, ["supply_plan","demand_impacts","schedule","shortages","alternatives","feasible","total_cost","late_order_count"], output=True)),
    "lot_recall_trace_v1": (_schema("lot_recall_trace_v1", {"start_lot":{"type":"string"},"links":{"type":"array"}}, ["start_lot","links"]), _schema("lot_recall_trace_v1", {"affected":{"type":"array"},"paths":{"type":"array"},"complete":{"type":"boolean"}}, ["affected","paths","complete"], output=True)),
}
for _asset in ASSET_DEFINITIONS:
    _asset["interface_key"] = f"manufacturing.{_asset['asset_key'].removesuffix('_v1')}"
    _asset["interface_version"] = "1.0.0"
    _asset["executor_type"] = "local"
    _asset["input_schema"], _asset["output_schema"] = _CONTRACTS[_asset["asset_key"]]

def _require(d: dict, fields: list[str]):
    missing = [f for f in fields if f not in d]
    if missing: raise ValueError("缺少输入字段: " + ", ".join(missing))


SUPPLIER_RESILIENCE_ASSET = {
    "asset_key": "supplier_resilience_v2", "name": "木材供应韧性",
    "kind": "what_if", "implementation": "supplier_resilience_v2",
    "description": "在固定 frePPLe 场景快照上计算有边界的确定性供应影响；不代表全局最优解。",
    "version": "2.1.0", "interface_key": "manufacturing.supplier_resilience",
    "interface_version": "2.1.0", "executor_type": "local",
    "input_schema": _schema("supplier_resilience_v2", {
        "objects": {"type": "array", "maxItems": 10000, "items": {"type": "object",
            "properties": {"type": {"type": "string"}, "id": {"type": "string"}, "properties": {"type": "object"}},
            "required": ["type", "id", "properties"], "additionalProperties": False}},
        "profile": {"type": "object"}, "scenario_time": {"type": "string"},
        "scope_items": {"type": "array", "minItems": 1, "items": {"type": "string"}},
    }, ["objects", "profile", "scenario_time", "scope_items"]),
    "output_schema": _schema("supplier_resilience_v2", {
        "metrics": {"type": "array"}, "demand_impacts": {"type": "array"},
        "supply_plan": {"type": "array"}, "warnings": {"type": "array"},
        "metric_contract": {"type": "string"}, "completeness": {"type": "string"},
        "exact": {"type": "boolean"}, "model_version": {"type": "string"},
    }, ["metrics", "demand_impacts", "supply_plan", "warnings", "metric_contract", "completeness"], output=True),
    "bindings": {"source": "server_bound_scenario_snapshot", "ontology_fields": ["Demand", "MaterialRequirement", "Inventory", "SupplyOption"]},
}
ASSET_DEFINITIONS.append(SUPPLIER_RESILIENCE_ASSET)

def _num(x, name):
    if isinstance(x, bool) or not isinstance(x, (int, float)): raise ValueError(f"{name} 必须是数字")
    return float(x)

def _release(x):
    _require(x, ["tool_condition","visual_inspection","machining_finalized","clamp_pressure"])
    violations=[]
    if str(x["tool_condition"]).lower() not in {"good","ok","acceptable"}: violations.append("tool_condition")
    if str(x["visual_inspection"]).lower() not in {"pass","passed","ok"}: violations.append("visual_inspection")
    if not bool(x["machining_finalized"]): violations.append("machining_finalized")
    p=_num(x["clamp_pressure"],"clamp_pressure")
    if not 38 <= p <= 42: violations.append("clamp_pressure(38..42)")
    return {"decision":"release" if not violations else "hold","violations":violations}

def _cycle(x):
    _require(x,["setup_seconds","unit_seconds","quantity"])
    setup=_num(x["setup_seconds"],"setup_seconds"); unit=_num(x["unit_seconds"],"unit_seconds"); q=_num(x["quantity"],"quantity")
    if setup < 0 or unit < 0 or q < 0 or int(q)!=q: raise ValueError("时间和数量必须为非负值，quantity 必须为整数")
    total=setup+unit*q
    return {"total_seconds":total,"total_minutes":total/60,"quantity":int(q)}

def _stats(x):
    _require(x,["values"]); values=x["values"]
    if not isinstance(values,list) or not values or any(not isinstance(v,(int,float)) for v in values): raise ValueError("values 必须是非空数字数组")
    n=len(values); mean=sum(values)/n; var=sum((v-mean)**2 for v in values)/n
    sx=sum(range(n)); sy=sum(values); sxx=sum(i*i for i in range(n)); sxy=sum(i*v for i,v in enumerate(values)); den=n*sxx-sx*sx
    slope=(n*sxy-sx*sy)/den if den else 0.0
    threshold=x.get("anomaly_threshold", None); rate=(sum(abs(v-mean)>float(threshold) for v in values)/n if threshold is not None else 0.0)
    return {"count":n,"mean":mean,"minimum":min(values),"maximum":max(values),"stdev":sqrt(var),"trend_slope":slope,"anomaly_rate":rate}

def _ml(x):
    _require(x,["temperature","vibration","pressure"])
    t=_num(x["temperature"],"temperature"); v=_num(x["vibration"],"vibration"); p=_num(x["pressure"],"pressure")
    contributions={"temperature":max(0,(t-70)/30)*0.5,"vibration":max(0,(v-2)/3)*0.35,"pressure":max(0,(15-p)/15)*0.15}
    score=max(0,min(1,sum(contributions.values())))
    level="high" if score>=0.7 else "medium" if score>=0.35 else "low"
    return {"score":score,"risk_level":level,"contributions":contributions,"model_version":"wear-risk-linear-1.0"}

def _assignment(x):
    _require(x,["tasks","resources"]); tasks=x["tasks"]; resources=x["resources"]
    if not tasks or not resources: raise ValueError("tasks/resources 不能为空")
    names=[r["id"] for r in resources]; caps={r["id"]:float(r.get("capacity",1e9)) for r in resources}; best=None
    choices=[]
    for t in tasks:
        eligible=t.get("eligible_resources",names); choices.append([r for r in names if r in eligible])
        if not choices[-1]: return {"assignments":[],"objective":None,"feasible":False,"reason":f"任务 {t.get('id')} 无合格资源"}
    for selected in product(*choices):
        loads={r:0.0 for r in names}
        for t,r in zip(tasks,selected): loads[r]+=float(t["duration"])
        if any(loads[r]>caps[r] for r in names): continue
        obj=max(loads.values())
        if best is None or obj<best[0]: best=(obj,selected,loads)
    if best is None: return {"assignments":[],"objective":None,"feasible":False,"reason":"资源容量不足"}
    return {"assignments":[{"task_id":t["id"],"resource_id":r} for t,r in zip(tasks,best[1])],"objective":best[0],"loads":best[2],"feasible":True}

def _plan(x):
    _require(x,["tasks"]); tasks=x["tasks"]; ids={t.get("id") for t in tasks}
    if len(ids)!=len(tasks) or None in ids: raise ValueError("任务 id 必须唯一")
    for t in tasks:
        for p in t.get("predecessors",[]):
            if p not in ids: raise ValueError(f"前置任务不存在: {p}")
    remaining={t["id"] for t in tasks}; end={}; resource_end={}; schedule=[]
    while remaining:
        ready=[t for t in tasks if t["id"] in remaining and all(p in end for p in t.get("predecessors",[]))]
        if not ready: raise ValueError("任务前置关系存在环")
        ready.sort(key=lambda t:(-float(t.get("priority",0)),t["id"]))
        for t in ready:
            start=max([end[p] for p in t.get("predecessors",[])] or [0]); duration=_num(t.get("duration",0),"duration")
            resource=t.get("resource","unassigned"); start=max(start, resource_end.get(resource, 0))
            finish=start+duration; end[t["id"]]=finish; schedule.append({"task_id":t["id"],"resource":resource,"start":start,"end":finish}); remaining.remove(t["id"])
            resource_end[resource]=finish
    return {"schedule":schedule,"makespan":max(end.values()) if end else 0,"feasible":True}

def _supplier_impact(x):
    available = {}
    for option in x["supply_options"]:
        quantity = float(option.get("quantity", 0))
        if quantity < 0:
            raise ValueError("Supplier quantity cannot be negative")
        if not option.get("disabled", False):
            available[option.get("item")] = available.get(option.get("item"), 0) + quantity

    bom_by_parent = {}
    for bom in x.get("boms", []):
        quantity = float(bom.get("quantity_per", 0))
        if quantity <= 0:
            raise ValueError("BOM quantity_per must be positive")
        bom_by_parent.setdefault(str(bom.get("parent_item")), []).append(
            (str(bom.get("component_item")), quantity)
        )

    requirements = []
    def explode(demand_id, item, quantity, path, depth=0):
        if depth > 20 or len(requirements) > 1000:
            raise ValueError("BOM expansion exceeded the bounded depth or row limit")
        if item in path:
            raise ValueError(f"BOM cycle detected at item {item}")
        components = bom_by_parent.get(item, [])
        if not components:
            requirements.append({"demand_id": demand_id, "item": item, "quantity": quantity, "path": path + [item]})
            return
        for component, per_parent in sorted(components):
            explode(demand_id, component, quantity * per_parent, path + [item], depth + 1)

    for demand in sorted(x["demands"], key=lambda item: str(item.get("id", ""))):
        quantity = float(demand.get("quantity", 0))
        if quantity < 0:
            raise ValueError("Demand quantity cannot be negative")
        explode(str(demand.get("id", "")), str(demand.get("item", "")), quantity, [])

    shortages = []
    for requirement in requirements:
        item = requirement["item"]
        available[item] = available.get(item, 0) - requirement["quantity"]
        if available[item] < 0:
            shortages.append({
                "demand_id": requirement["demand_id"],
                "item": item,
                "quantity": round(-available[item], 6),
                "path": requirement["path"],
            })
    alternatives = []
    for option in sorted(x["supply_options"], key=lambda item: (str(item.get("item")), str(item.get("supplier")))):
        if option.get("disabled", False): alternatives.append({"supplier": option.get("supplier"), "item": option.get("item"), "lead_time": option.get("lead_time")})
    return {"shortages": shortages, "alternatives": alternatives, "feasible": not shortages}

def _capacity_replan(x):
    schedule = []
    resource_end = {}
    late = []
    makespan = 0.0
    tasks = sorted(x["tasks"], key=lambda item: (str(item.get("due_date", "")), str(item.get("id", ""))))
    for task in tasks:
        duration = float(task.get("duration", 0))
        resource = str(task.get("resource") or "unassigned")
        start = resource_end.get(resource, 0.0)
        end = start + duration
        resource_end[resource] = end
        makespan = max(makespan, end)
        schedule.append({"task_id": task.get("id"), "start": start, "end": end, "resource": resource})
        if task.get("due_at") is not None and end > float(task["due_at"]):
            late.append(task.get("id"))
    return {
        "schedule": schedule,
        "late_tasks": late,
        "makespan": makespan,
        "feasible": makespan <= float(x["capacity"]),
    }


def _supply_chain_reasoning(x):
    """Run a bounded, explainable supply-to-delivery scenario.

    The executor intentionally uses a deterministic earliest-arrival/lowest-cost
    allocation heuristic. It reports feasibility and impact evidence; it does not
    claim global optimality. This keeps the demo reproducible while leaving a
    replaceable boundary for a future CP-SAT implementation.
    """
    from datetime import datetime, timedelta
    from collections import defaultdict

    try:
        anchor = datetime.fromisoformat(str(x["scenario_time"]).replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("scenario_time must be an ISO datetime") from exc
    if len(x["demands"]) > 500 or len(x["supply_options"]) > 5000 or len(x["boms"]) > 2000:
        raise ValueError("supply-chain scenario exceeds bounded input size")

    bom = defaultdict(list)
    for row in x["boms"]:
        parent, component = str(row.get("parent_item", "")), str(row.get("component_item", ""))
        quantity = _num(row.get("quantity_per", 0), "BOM quantity_per")
        if not parent or not component or quantity <= 0:
            raise ValueError("BOM rows require positive parent_item, component_item and quantity_per")
        bom[parent].append((component, quantity))

    def explode(item, quantity, path=()):
        if item in path:
            raise ValueError(f"BOM cycle detected at item {item}")
        children = bom.get(item, [])
        if not children:
            return {item: quantity}
        result = defaultdict(float)
        for child, factor in children:
            for leaf, amount in explode(child, quantity * factor, path + (item,)).items():
                result[leaf] += amount
        return dict(result)

    inventory = defaultdict(float)
    for row in x["inventory"]:
        amount = _num(row.get("quantity", row.get("onhand", 0)), "inventory quantity")
        inventory[str(row.get("item", row.get("item_id", "")))] += amount

    def arrival(option):
        lead = _num(option.get("lead_time", option.get("lead_days", 0)), "lead_time")
        outage_until = option.get("outage_until")
        if outage_until:
            outage = datetime.fromisoformat(str(outage_until).replace("Z", "+00:00"))
            if outage > anchor:
                return outage + timedelta(days=lead)
        return anchor + timedelta(days=lead)

    options_by_item = defaultdict(list)
    for option in x["supply_options"]:
        item = str(option.get("item", ""))
        quantity = _num(option.get("quantity", option.get("capacity", 0)), "supply quantity")
        if not item or quantity < 0:
            raise ValueError("supply options require item and non-negative quantity")
        if option.get("disabled") or option.get("qualified", True) is False:
            continue
        row = dict(option)
        row["item"] = item
        row["_remaining"] = quantity
        row["_arrival"] = arrival(option)
        row["_unit_cost"] = _num(option.get("unit_cost", option.get("cost", 0)), "unit_cost")
        options_by_item[item].append(row)
    for values in options_by_item.values():
        values.sort(key=lambda item: (item["_arrival"], item["_unit_cost"], str(item.get("supplier", ""))))

    all_options = [row for row in x["supply_options"] if not row.get("disabled")]
    alternatives = []
    for row in sorted(all_options, key=lambda item: (str(item.get("item", "")), str(item.get("supplier", "")))):
        if row.get("qualified", True) is False:
            continue
        alternatives.append({"supplier": row.get("supplier"), "item": row.get("item"), "lead_time": row.get("lead_time", row.get("lead_days", 0)), "qualified": True})

    remaining_inventory = dict(inventory)
    supply_plan = []
    impacts = []
    schedule = []
    resource_end = defaultdict(lambda: anchor)
    total_cost = 0.0
    shortages = []

    def due_at(demand):
        due = demand.get("due_at", demand.get("due_date", demand.get("due")))
        if due is None:
            return anchor + timedelta(days=9999)
        if isinstance(due, (int, float)):
            return anchor + timedelta(days=float(due))
        parsed = datetime.fromisoformat(str(due).replace("Z", "+00:00"))
        if anchor.tzinfo and parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=anchor.tzinfo)
        return parsed

    for demand in sorted(x["demands"], key=lambda row: (due_at(row), -float(row.get("priority", 0)), str(row.get("id", "")))):
        demand_id = str(demand.get("id", ""))
        item = str(demand.get("item", ""))
        quantity = _num(demand.get("quantity", 0), "demand quantity")
        if not demand_id or not item or quantity < 0:
            raise ValueError("demands require id, item and non-negative quantity")
        requirements = explode(item, quantity)
        ready_at = anchor
        used = []
        order_shortages = []
        order_cost = 0.0
        for material, required in sorted(requirements.items()):
            from_stock = min(required, remaining_inventory.get(material, 0.0))
            remaining_inventory[material] = remaining_inventory.get(material, 0.0) - from_stock
            remaining = required - from_stock
            material_ready = anchor if from_stock else anchor
            if remaining > 0:
                for option in options_by_item.get(material, []):
                    if remaining <= 1e-9:
                        break
                    used_quantity = min(remaining, option["_remaining"])
                    if used_quantity <= 0:
                        continue
                    option["_remaining"] -= used_quantity
                    remaining -= used_quantity
                    material_ready = max(material_ready, option["_arrival"])
                    line_cost = used_quantity * option["_unit_cost"]
                    order_cost += line_cost
                    total_cost += line_cost
                    used.append({"item": material, "supplier": option.get("supplier"), "quantity": round(used_quantity, 6), "arrival": option["_arrival"].isoformat(), "unit_cost": option["_unit_cost"]})
            if remaining > 1e-9:
                order_shortages.append({"item": material, "quantity": round(remaining, 6), "path": [item, material]})
                shortages.append({"demand_id": demand_id, "item": material, "quantity": round(remaining, 6), "path": [item, material]})
            ready_at = max(ready_at, material_ready)
            if from_stock:
                used.append({"item": material, "source": "inventory", "quantity": round(from_stock, 6), "arrival": anchor.isoformat(), "unit_cost": 0})
        duration = _num(demand.get("processing_days", demand.get("duration", 1)), "processing_days")
        resource = str(demand.get("resource", "default"))
        start = max(ready_at, resource_end[resource])
        finish = start + timedelta(days=duration)
        resource_end[resource] = finish
        due = due_at(demand)
        late_days = max(0, (finish - due).total_seconds() / 86400)
        late_days = int(ceil(late_days))
        schedule.append({"demand_id": demand_id, "resource": resource, "start": start.isoformat(), "finish": finish.isoformat(), "duration_days": duration})
        impacts.append({"demand_id": demand_id, "item": item, "due": due.isoformat(), "material_ready": ready_at.isoformat(), "finish": finish.isoformat(), "late_days": late_days, "shortages": order_shortages, "procurement_cost": round(order_cost, 6), "used_sources": used, "reason_codes": (["material_shortage"] if order_shortages else []) + (["late_delivery"] if late_days else [])})

    for item, values in options_by_item.items():
        for option in values:
            supply_plan.append({"supplier": option.get("supplier"), "item": item, "allocated": round(float(option.get("quantity", 0)) - option["_remaining"], 6), "remaining": round(option["_remaining"], 6), "arrival": option["_arrival"].isoformat(), "unit_cost": option["_unit_cost"]})
    late_order_count = sum(1 for row in impacts if row["late_days"] > 0)
    return {"supply_plan": supply_plan, "demand_impacts": impacts, "schedule": schedule, "shortages": shortages, "alternatives": alternatives, "feasible": not shortages, "total_cost": round(total_cost, 6), "late_order_count": late_order_count}

def _lot_recall(x):
    adjacency={}
    for link in x["links"]: adjacency.setdefault(str(link.get("source")), []).append(str(link.get("target")))
    seen={str(x["start_lot"])}; queue=[(str(x["start_lot"]), [str(x["start_lot"])])]; paths=[]
    while queue and len(seen) <= 1000:
        node, path=queue.pop(0)
        for target in sorted(adjacency.get(node, [])):
            paths.append(path+[target])
            if target not in seen: seen.add(target); queue.append((target, path+[target]))
    return {"affected": sorted(seen), "paths": paths, "complete": not queue}

def _uci_steel_energy_tariff(inputs):
    from decimal import Decimal
    from .uci_steel_energy import reviewed_energy_profile, tariff_sensitivity
    result = tariff_sensitivity(reviewed_energy_profile(), inputs["rates_per_kwh"],
                                maximum_load_rate_change=inputs["maximum_load_rate_change"])
    return {
        "baseline_cost": float(Decimal(result["baseline_cost"])),
        "candidate_cost": float(Decimal(result["candidate_cost"])),
        "cost_delta": float(Decimal(result["cost_delta"])),
        "usage_kwh_before": float(Decimal(result["usage_kwh_before"])),
        "usage_kwh_after": float(Decimal(result["usage_kwh_after"])),
        "usage_delta_kwh": 0.0,
        "dataset_sha256": result["dataset_sha256"],
        "rates_are_illustrative": result["rates_are_illustrative"],
    }


EXECUTORS={"release_gate_v1":_release,"cycle_time_v1":_cycle,"uci_steel_energy_tariff_v1":_uci_steel_energy_tariff,"observation_summary_v1":_stats,"tool_wear_risk_v1":_ml,"resource_assignment_v1":_assignment,"priority_plan_v1":_plan,"supplier_disruption_impact_v1":_supplier_impact,"capacity_replan_v1":_capacity_replan,"supply_chain_reasoning_v1":_supply_chain_reasoning,"lot_recall_trace_v1":_lot_recall}


def _supplier_resilience(inputs):
    from types import SimpleNamespace
    from .supplier_resilience import calculate
    snapshot = SimpleNamespace(objects={(row["type"], row["id"]): row["properties"] for row in inputs["objects"]})
    return calculate(snapshot, inputs["profile"], inputs["scenario_time"], inputs["scope_items"])


EXECUTORS["supplier_resilience_v2"] = _supplier_resilience

def execute(asset: dict, inputs: dict, *, metadata=None, snapshot=None, ontology_id=None, through_action=False) -> dict:
    from .function_contracts import execution_class, validate_domain, validate_edits, fail
    from .object_query.logic_binding import reject_page_binding
    reject_page_binding(asset, inputs)
    asset = normalize_contract(asset)
    kind = execution_class(asset)
    if kind == 'edit' and not through_action:
        fail('execution', 'Edit-producing Functions must execute through an Action')
    impl=asset.get("implementation") or asset.get("asset_key")
    if impl not in EXECUTORS: raise ValueError(f"未注册本地实现: {impl}")
    normalized, unit_trace = normalize_units(asset["input_schema"], inputs)
    validate_payload(asset["input_schema"], normalized, "input")
    validate_domain(asset['input_schema'], normalized, metadata=metadata, snapshot=snapshot, ontology_id=ontology_id)
    output=EXECUTORS[impl](normalized)
    validate_payload(asset["output_schema"], output, "output")
    validate_domain(asset['output_schema'], output, metadata=metadata, snapshot=snapshot, ontology_id=ontology_id)
    if kind == 'edit':
        validate_edits(asset, output, ontology_id)
    return {"asset_key":asset.get("asset_key"),"interface_key":asset.get("interface_key"),"version":asset.get("version","1.0.0"),"implementation":impl,"inputs_bound":list(normalized),"output":output,"trace":[{"stage":"bind","field_count":len(normalized)}, *unit_trace, {"stage":"execute","implementation":impl},{"stage":"validate","status":"passed"}]}
