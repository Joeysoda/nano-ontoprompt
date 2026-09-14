"""Agno-facing tools and a deterministic, auditable decision proposal path.

The deterministic path is deliberately kept as a first-class mode: it makes
100+ regression cases reproducible while the real Agno/LLM mode is reserved
for representative tool-selection and language-quality checks.
"""
from __future__ import annotations

import json
import os
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.v2.reasoning import ReasoningRun
from app.models.v2.logic import OntologyLogicRule
from app.services.v2.graph.falkordb_service import FalkorDBService


class AgentToolbox:
    def __init__(self, ontology_id: str, db: Session):
        self.ontology_id = ontology_id
        self.db = db
        self.trace: list[dict[str, Any]] = []

    def ontology_context(self) -> dict[str, Any]:
        ontology = self.db.get(OntologyProject, self.ontology_id)
        if not ontology:
            raise ValueError("本体不存在")
        result = {
            "ontology_id": self.ontology_id,
            "name": ontology.name,
            "domain": ontology.domain,
            "data_class": ontology.data_class,
            "revision_id": ontology.current_revision_id,
        }
        self.trace.append({"tool": "ontology_context", "result": result})
        return result

    def object_context(self, object_id: str | None = None) -> dict[str, Any]:
        if not object_id:
            result = {"objects": [], "reason": "未指定对象"}
            self.trace.append({"tool": "object_context", "result": result})
            return result
        graph = FalkorDBService()
        if not graph.available:
            raise RuntimeError("FalkorDB 不可用，无法读取业务对象")
        rows = graph._graph(self.ontology_id).query(
            "MATCH (n:Instance {_instance_id:$id}) RETURN n",
            params={"id": object_id},
        ).result_set
        if not rows:
            raise ValueError(f"业务对象不存在：{object_id}")
        node = rows[0][0]
        props = dict(getattr(node, "properties", node))
        result = {"objects": [{"id": object_id, "type": props.get("_type"), "properties": props}]}
        self.trace.append({"tool": "object_context", "result": result})
        return result

    def discover_context(self, task: str) -> dict[str, Any]:
        from app.services.v2.agent_context import context_for
        return context_for(self, task)

    def reasoning_context(self, run_id: str | None = None, conclusion: str | None = None) -> dict[str, Any]:
        query = self.db.query(ReasoningRun).filter_by(ontology_id=self.ontology_id)
        run = query.filter_by(id=run_id).first() if run_id else query.order_by(ReasoningRun.created_at.desc()).first()
        if not run:
            result = {"run": None, "conclusions": [], "reason": "没有推理运行记录"}
            self.trace.append({"tool": "reasoning_context", "result": result})
            return result
        conclusions = (run.payload or {}).get("conclusions", [])
        if conclusion:
            conclusions = [item for item in conclusions if item.get("conclusion") == conclusion]
            if not conclusions:
                raise ValueError("指定结论不属于该推理运行")
        result = {
            "run": {"id": run.id, "status": run.status, "revision_id": (run.payload or {}).get("ontology_revision_id")},
            "conclusions": conclusions[:20],
        }
        self.trace.append({"tool": "reasoning_context", "result": result})
        return result

    def evaluate_decision_rules(self, objects: list[dict[str, Any]]) -> dict[str, Any]:
        """Evaluate published decision policies against grounded object properties."""
        rules = self.db.query(OntologyLogicRule).filter(
            OntologyLogicRule.ontology_id == self.ontology_id,
            OntologyLogicRule.logic_type == "decision",
            OntologyLogicRule.enabled.is_(True),
            OntologyLogicRule.status == "published",
        ).all()
        matches, missing, evaluated = [], [], []
        for rule in rules:
            expr = rule.expression or {}
            groups = expr.get("any_of") or [expr.get("all_of") or expr.get("conditions") or []]
            rule_matches, rule_missing, object_results = [], [], []
            for obj in objects:
                props = obj.get("properties") or {}
                alternatives = []
                for conditions in groups:
                    checks = []
                    for condition in conditions:
                        field, op, expected = condition.get("field"), condition.get("op", "equals"), condition.get("value")
                        actual = props.get(field)
                        if actual is None:
                            checks.append({"field": field, "passed": False, "missing": True, "expected": expected})
                            continue
                        try:
                            if op == "equals": passed = str(actual).lower() == str(expected).lower()
                            elif op == "gte": passed = float(actual) >= float(expected)
                            elif op == "lt": passed = float(actual) < float(expected)
                            else: passed = False
                        except (TypeError, ValueError):
                            passed = False
                        checks.append({"field": field, "actual": actual, "expected": expected, "passed": passed})
                    matched = bool(checks) and all(c["passed"] for c in checks)
                    alternatives.append({"matched": matched, "checks": checks})
                    entry = {"rule_id": rule.id, "name": rule.name, "outcome": expr.get("outcome"),
                             "action": expr.get("action"), "explanation": rule.description,
                             "object_id": obj.get("id"), "checks": checks, "priority": expr.get("priority", 0)}
                    if matched:
                        rule_matches.append(entry)
                    elif any(c.get("missing") for c in checks):
                        rule_missing.append({**entry, "checks": checks})
                object_results.append({"object_id": obj.get("id"), "alternatives": alternatives})
            status = "已命中" if rule_matches else "信息缺失" if rule_missing else "未命中"
            evaluated.append({"rule_id": rule.id, "name": rule.name, "outcome": expr.get("outcome"),
                              "explanation": rule.description, "status": status, "objects": object_results})
            matches.extend(rule_matches)
            missing.extend(rule_missing)
        matches.sort(key=lambda x: x.get("priority", 0), reverse=True)
        if matches:
            priority = matches[0].get("priority", 0)
            top = [item for item in matches if item.get("priority", 0) == priority]
            outcomes = {item.get("outcome") for item in top}
            if len(outcomes) > 1:
                matches = [{**top[0], "outcome": "规则冲突，需人工复核", "conflict": True,
                            "conflicting_rules": [item["rule_id"] for item in top]}] + matches[len(top):]
            else:
                matches = top
        result = {"matches": matches, "missing": missing, "evaluated": evaluated, "rule_count": len(rules)}
        self.trace.append({"tool": "evaluate_decision_rules", "result": {"matches": len(matches), "missing": len(missing), "rules": len(rules)}})
        return result

    def propose(self, task: str, object_id: str | None = None, run_id: str | None = None,
                conclusion: str | None = None, mode: str = "deterministic") -> dict[str, Any]:
        """Produce a structured proposal; no decision or graph write occurs here."""
        ontology = self.ontology_context()
        objects = self.object_context(object_id)
        reasoning = self.reasoning_context(run_id, conclusion)
        selected = reasoning["conclusions"]
        if not selected:
            raise ValueError("没有可引用的规则推理结论，无法生成可审计决策")
        proof = selected[0]
        subject = object_id or (proof.get("bindings") or {}).get("observation") or "相关业务对象"
        proposal = {
            "category": "industrial_maintenance",
            "scenario": task,
            "reasoning": f"基于推理结论 {proof['conclusion']}，建议对 {subject} 进行人工复核；此建议仍需用户确认。",
            "outcome": "proposed_for_review",
            "confidence": 0.0,
            "decision_maker": "agno_agent",
            "status": "proposed",
            "entity_ids": [object_id] if object_id else [],
            "basis": [{"run_id": reasoning["run"]["id"], "conclusion": proof["conclusion"]}],
            "evidence": selected,
            "ontology_id": ontology["ontology_id"],
            "ontology_revision_id": ontology["revision_id"],
            "agent_mode": mode,
        }
        self.trace.append({"tool": "propose_decision", "result": {"status": "proposed", "basis_count": len(selected)}})
        return proposal


def run_agno(toolbox: AgentToolbox, task: str, object_id: str | None, run_id: str | None,
             conclusion: str | None) -> dict[str, Any]:
    """Run a real Agno agent when configured; fail explicitly when unavailable."""
    try:
        from agno.agent import Agent
    except ImportError as exc:
        raise RuntimeError("Agno 未安装；请安装 backend/requirements-agent.txt") from exc
    model_id = os.getenv("AGNO_MODEL_ID", "gpt-4o-mini")
    if os.getenv("AZURE_OPENAI_API_KEY") and os.getenv("AZURE_OPENAI_ENDPOINT"):
        try:
            from agno.models.azure.openai_chat import AzureOpenAI
        except ImportError as exc:
            raise RuntimeError("Azure Agno model adapter is unavailable; install backend/requirements-agent.txt") from exc
        model = AzureOpenAI(
            id=model_id,
            api_version=os.getenv("AZURE_OPENAI_API_VERSION", "2024-10-21"),
            azure_endpoint=os.environ["AZURE_OPENAI_ENDPOINT"],
            azure_deployment=os.getenv("AZURE_OPENAI_DEPLOYMENT", model_id),
        )
    else:
        try:
            from agno.models.openai import OpenAIChat
        except ImportError as exc:
            raise RuntimeError("OpenAI/Agno model dependency is missing; install backend/requirements-agent.txt") from exc
        model = OpenAIChat(id=model_id)
    agent = Agent(
        model=model,
        tools=[toolbox.ontology_context, toolbox.object_context, toolbox.reasoning_context],
        instructions=[
            "只根据工具返回的对象、规则结论和证据生成建议。",
            "输出 JSON，字段为 category, scenario, reasoning, outcome, entity_ids, basis。",
            "不得声称已执行现实动作；所有建议状态必须是 proposed。",
        ],
        markdown=False,
    )
    try:
        response = agent.run(task)
    except Exception as exc:
        raise RuntimeError(f"Agno model call failed: {exc}") from exc
    content = getattr(response, "content", response)
    if isinstance(content, dict):
        proposal = content
    else:
        match = re.search(r"\{.*\}", str(content), re.S)
        if not match:
            raise ValueError("Agno 未返回可解析的结构化决策")
        proposal = json.loads(match.group(0))
    base = toolbox.propose(task, object_id, run_id, conclusion, mode="agno")
    for key in ("category", "scenario", "reasoning", "outcome", "entity_ids", "basis"):
        if key in proposal:
            base[key] = proposal[key]
    base["status"] = "proposed"
    return base
