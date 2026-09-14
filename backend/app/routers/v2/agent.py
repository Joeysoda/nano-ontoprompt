"""Agno decision assistant endpoints."""
from datetime import datetime, timezone
from typing import Literal
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.deps import get_db, get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.v2.agent import AgentRun
from app.models.v2.decision import DecisionRecord
from app.services.v2.agent_decision import AgentToolbox, run_agno
from app.services.v2.agent_context import context_for

router = APIRouter(dependencies=[Depends(get_current_user)])


class AgentRunInput(BaseModel):
    task: str = Field(min_length=5, max_length=4000)
    object_id: str | None = None
    reasoning_run_id: str | None = None
    conclusion: str | None = None
    mode: Literal["deterministic", "agno"] = "deterministic"


class ContextDraftInput(BaseModel):
    task: str = Field(min_length=5, max_length=4000)


class ContextUpdateInput(BaseModel):
    object_ids: list[str] = Field(default_factory=list, max_length=50)
    reasoning_run_id: str | None = None
    conclusions: list[str] = Field(default_factory=list, max_length=50)


def scope(db: Session, oid: str, user):
    item = db.get(OntologyProject, oid)
    if not item or (user.role != "admin" and item.created_by != user.id):
        raise HTTPException(404, "本体不存在或无访问权限")
    return item


@router.post("/{oid}/agent/runs")
def create_agent_run(oid: str, body: AgentRunInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ontology = scope(db, oid, user)
    toolbox = AgentToolbox(oid, db)
    try:
        proposal = (run_agno(toolbox, body.task, body.object_id, body.reasoning_run_id, body.conclusion)
                    if body.mode == "agno" else toolbox.propose(body.task, body.object_id, body.reasoning_run_id, body.conclusion))
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = {
        "task": body.task,
        "mode": body.mode,
        "object_id": body.object_id,
        "reasoning_run_id": body.reasoning_run_id,
        "conclusion": body.conclusion,
        "proposal": {**proposal, "ontology_revision_id": ontology.current_revision_id},
        "tool_trace": toolbox.trace,
        "created_by": user.id,
    }
    run = AgentRun(ontology_id=oid, created_by=user.id, status="proposed", payload=payload)
    db.add(run)
    db.commit()
    return {"run_id": run.id, "status": run.status, **payload}


@router.post("/{oid}/agent/context-drafts")
def create_context_draft(oid: str, body: ContextDraftInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ontology = scope(db, oid, user)
    toolbox = AgentToolbox(oid, db)
    try:
        context = toolbox.discover_context(body.task)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    payload = {"task": body.task, "mode": "natural_language", "context": context, "tool_trace": toolbox.trace, "created_by": user.id, "ontology_revision_id": ontology.current_revision_id}
    run = AgentRun(ontology_id=oid, created_by=user.id, status="context_review", payload=payload)
    db.add(run)
    db.commit()
    return {"run_id": run.id, "status": run.status, **payload}


@router.post("/{oid}/agent/context-preview")
def preview_context(oid: str, body: ContextUpdateInput, db: Session = Depends(get_db), user=Depends(get_current_user)):
    scope(db, oid, user)
    try:
        return context_for(AgentToolbox(oid, db), "", body.reasoning_run_id, body.object_ids)
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc


@router.patch("/{oid}/agent/runs/{run_id}/context")
def update_agent_context(oid: str, run_id: str, body: ContextUpdateInput, db: Session = Depends(get_db), user=Depends(require_editor)):
    scope(db, oid, user)
    run = db.query(AgentRun).filter_by(id=run_id, ontology_id=oid).first()
    if not run:
        raise HTTPException(404, "Agent run not found")
    if run.status != "context_review":
        raise HTTPException(409, "Only a context-review run can be edited")
    toolbox = AgentToolbox(oid, db)
    try:
        context = context_for(toolbox, run.payload["task"], body.reasoning_run_id, body.object_ids)
        available = {p["conclusion"] for p in context["conclusions"]}
        if set(body.conclusions) - available:
            raise ValueError("结论不属于所选运行或没有与所选对象关联的证明")
        context["conclusions"] = [p for p in context["conclusions"] if p["conclusion"] in body.conclusions]
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    run.payload = {**run.payload, "context": context, "context_edited": True}
    db.commit()
    return {"run_id": run.id, "status": run.status, "context": context}


@router.post("/{oid}/agent/runs/{run_id}/generate")
def generate_agent_proposal(oid: str, run_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    ontology = scope(db, oid, user)
    run = db.query(AgentRun).filter_by(id=run_id, ontology_id=oid).first()
    if not run:
        raise HTTPException(404, "Agent run not found")
    if run.status != "context_review":
        raise HTTPException(409, "Only a context-review run can generate a proposal")
    context = (run.payload or {}).get("context") or {}
    objects = context.get("objects") or []
    reasoning_run = context.get("reasoning_run") or {}
    conclusions = context.get("conclusions") or []
    conclusion = conclusions[0].get("conclusion") if conclusions else None
    if not reasoning_run.get("id") or not conclusion:
        raise HTTPException(422, "Please approve at least one reasoning conclusion")
    toolbox = AgentToolbox(oid, db)
    try:
        verified = context_for(toolbox, run.payload["task"], reasoning_run["id"], [o["id"] for o in objects])
        selected_values = {p["conclusion"] for p in conclusions}
        selected_proofs = [p for p in verified["conclusions"] if p["conclusion"] in selected_values]
        if {p["conclusion"] for p in selected_proofs} != selected_values:
            raise ValueError("上下文已变化或结论没有关联证明，请重新审核")
        verified_objects = verified["objects"]
        evaluation = toolbox.evaluate_decision_rules(verified_objects)
        matched = evaluation["matches"]
        outcomes = {item.get("outcome") for item in matched if item.get("outcome")}
        if len(outcomes) > 1:
            outcome = "规则冲突，需人工复核"
            reasoning_text = "当前证据同时触发了不同的已发布决策规则，系统不会擅自选择其中一条。请检查规则优先级或交由负责人复核。"
        elif matched:
            outcome = next(iter(outcomes))
            reasoning_text = matched[0].get("explanation") or "该建议由已发布决策规则根据当前对象属性生成。"
        elif evaluation["missing"]:
            missing_fields = sorted({c["field"] for item in evaluation["missing"] for c in item["checks"] if c.get("missing") and c.get("field")})
            outcome = "关键信息缺失，暂缓放行"
            reasoning_text = "已发布规则需要以下数据，但当前对象没有提供：" + "、".join(missing_fields)
        else:
            outcome = "当前证据未触发决策规则，需人工复核"
            reasoning_text = "系统检查了当前本体中已发布的决策规则；现有观测未满足任何一条规则的触发条件，因此不给出放行、点检或停机结论。"
        proposal = {
            "scenario": run.payload["task"], "status": "proposed",
            "outcome": outcome,
            "reasoning": reasoning_text,
            "entity_ids": [o["id"] for o in verified_objects],
            "basis": [{"run_id": reasoning_run["id"], "conclusion": p["conclusion"]} for p in selected_proofs],
            "evidence": selected_proofs, "agent_mode": "deterministic",
            "decision_rule_evaluation": evaluation,
            "decision_maker": "evidence_review", "ontology_revision_id": ontology.current_revision_id,
        }
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(422, str(exc)) from exc
    run.payload = {**run.payload, "proposal": {**proposal, "ontology_revision_id": ontology.current_revision_id}, "tool_trace": toolbox.trace}
    run.status = "proposed"
    db.commit()
    return {"run_id": run.id, "status": run.status, "proposal": run.payload["proposal"], "tool_trace": run.payload["tool_trace"]}


@router.get("/{oid}/agent/runs")
def list_agent_runs(oid: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    scope(db, oid, user)
    rows = db.query(AgentRun).filter_by(ontology_id=oid).order_by(AgentRun.created_at.desc()).limit(100).all()
    return [{"run_id": r.id, "status": r.status, "created_at": r.created_at.isoformat(),
             "task": (r.payload or {}).get("task"), "mode": (r.payload or {}).get("mode")}
            for r in rows]


@router.get("/{oid}/agent/runs/{run_id}")
def get_agent_run(oid: str, run_id: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    scope(db, oid, user)
    run = db.query(AgentRun).filter_by(id=run_id, ontology_id=oid).first()
    if not run:
        raise HTTPException(404, "Agent 运行不存在")
    return {"run_id": run.id, "status": run.status, **(run.payload or {})}


@router.post("/{oid}/agent/runs/{run_id}/confirm")
def confirm_agent_run(oid: str, run_id: str, db: Session = Depends(get_db), user=Depends(require_editor)):
    ontology = scope(db, oid, user)
    run = db.query(AgentRun).filter_by(id=run_id, ontology_id=oid).first()
    if not run:
        raise HTTPException(404, "Agent 运行不存在")
    if run.status == "confirmed":
        return {"status": run.status, "decision_id": (run.payload or {}).get("decision_id")}
    if run.status != "proposed":
        raise HTTPException(409, "该 Agent 运行不能确认")
    proposal = dict((run.payload or {}).get("proposal") or {})
    for ident in proposal.get("entity_ids", []):
        if not isinstance(ident, str) or not ident:
            raise HTTPException(422, "决策关联对象无效")
    decision_payload = {
        **proposal,
        "status": "confirmed",
        "agent_run_id": run.id,
        "created_by": user.id,
        "confirmed_at": datetime.now(timezone.utc).isoformat(),
        "ontology_revision_id": ontology.current_revision_id,
    }
    item = DecisionRecord(ontology_id=oid, payload=decision_payload)
    db.add(item)
    db.flush()
    run.payload = {**run.payload, "decision_id": item.id}
    run.status = "confirmed"
    db.commit()
    return {"status": run.status, "decision_id": item.id, "decision": {"id": item.id, **decision_payload}}
