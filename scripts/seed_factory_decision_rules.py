"""Idempotently install three reviewed demo policies for the FactoryNet ontology."""
from app.database import SessionLocal
from app.models.v2.logic import OntologyLogicRule

ONTOLOGY = "e69354c8-6db7-4da1-b551-33b41f4c0315"
RULES = [
    {
        "name": "下一批生产放行",
        "description": "演示规则（非经核定的生产安全标准）：只有刀具标注未磨损、视觉检查通过、上一批加工完成且夹具压力不低于 3.5 bar 时，才建议进入下一批生产。3.5 bar 为本次功能验证阈值，须由工艺负责人确认。",
        "expression": {"all_of": [
            {"field": "ctx_tool_condition", "op": "equals", "value": "unworn"},
            {"field": "ctx_passed_visual_inspection", "op": "equals", "value": "yes"},
            {"field": "ctx_machining_finalized", "op": "equals", "value": "yes"},
            {"field": "ctx_clamp_pressure", "op": "gte", "value": 3.5},
        ], "outcome": "建议放行进入下一批生产", "action": "allow_next_batch", "priority": 30},
        "severity": "info",
    },
    {
        "name": "刀具异常安排点检",
        "description": "演示规则（需工艺负责人核定）：刀具状态标记为已磨损或未知时，建议安排人工点检；该规则不会把未磨损状态判成故障。",
        "expression": {"any_of": [[
            {"field": "ctx_tool_condition", "op": "equals", "value": "worn"}], [
            {"field": "ctx_tool_condition", "op": "equals", "value": "unknown"}]],
            "outcome": "建议安排人工点检", "action": "schedule_inspection", "priority": 60},
        "severity": "warning",
    },
    {
        "name": "加工风险暂停设备",
        "description": "演示规则（非经核定的停机标准）：视觉检查失败、上一批加工未完成或夹具压力低于 3.0 bar 任一条件成立时，建议暂停设备并复核。3.0 bar 为本次功能验证阈值，须由工艺/安全负责人确认。",
        "expression": {"any_of": [[
            {"field": "ctx_passed_visual_inspection", "op": "equals", "value": "no"}], [
            {"field": "ctx_machining_finalized", "op": "equals", "value": "no"}], [
            {"field": "ctx_clamp_pressure", "op": "lt", "value": 3.0}]],
            "outcome": "建议暂停设备并复核", "action": "hold_machine", "priority": 100},
        "severity": "critical",
    },
]

db = SessionLocal()
try:
    for spec in RULES:
        rule = db.query(OntologyLogicRule).filter_by(ontology_id=ONTOLOGY, name=spec["name"]).first()
        if rule:
            rule.description = spec["description"]
            rule.expression = spec["expression"]
            rule.logic_type = "decision"
            rule.target_entity_type = "Observation"
            rule.severity = spec["severity"]
            rule.enabled = True
            rule.status = "published"
        else:
            db.add(OntologyLogicRule(
                ontology_id=ONTOLOGY, name=spec["name"], logic_type="decision",
                description=spec["description"], target_entity_type="Observation",
                expression=spec["expression"], severity=spec["severity"],
                enabled=True, status="published", version=1,
            ))
    db.commit()
    print("Published 3 FactoryNet decision policies.")
finally:
    db.close()
