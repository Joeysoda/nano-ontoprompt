"""Evaluate installed policies with controlled profiles; profiles are never persisted."""
from app.database import SessionLocal
from app.services.v2.agent_decision import AgentToolbox

ONTOLOGY = "e69354c8-6db7-4da1-b551-33b41f4c0315"
PROFILES = [
    ("真实 FactoryNet 放行样本", {"ctx_tool_condition": "unworn", "ctx_passed_visual_inspection": "yes", "ctx_machining_finalized": "yes", "ctx_clamp_pressure": 4.0}, "建议放行进入下一批生产"),
    ("规则边界样本：刀具磨损", {"ctx_tool_condition": "worn", "ctx_passed_visual_inspection": "yes", "ctx_machining_finalized": "yes", "ctx_clamp_pressure": 4.0}, "建议安排人工点检"),
    ("规则边界样本：夹具压力偏低", {"ctx_tool_condition": "unworn", "ctx_passed_visual_inspection": "yes", "ctx_machining_finalized": "yes", "ctx_clamp_pressure": 2.5}, "建议暂停设备并复核"),
]

db = SessionLocal()
try:
    toolbox = AgentToolbox(ONTOLOGY, db)
    for name, props, expected in PROFILES:
        result = toolbox.evaluate_decision_rules([{"id": name, "properties": props}])
        outcomes = {item["outcome"] for item in result["matches"]}
        assert outcomes == {expected}, (name, outcomes, expected)
        print(f"PASS {name}: {expected}")
finally:
    db.close()
