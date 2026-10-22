"""LLM explanations of a run's candidate action, grounded and checked.

The model sees only evidence assembled from the run output (baseline, the
candidate's re-simulated result, its actions, affected orders and the
coverage of the parts involved).  It must cite evidence ids for every claim.
The platform then checks, without the model, that every cited id exists and
that every number in a claim appears in the cited evidence.  An explanation
that fails the check is stored and shown as unverified, never as fact.
There is no template fallback: if the model is unavailable the call fails.
"""
from __future__ import annotations

import json
import os
import re
from hashlib import sha256

import httpx

DEFAULT_MODEL = "gpt-4.1"
# ASCII-only boundary: \w would treat CJK characters as word characters and split "提升198,286.78".
NUMBER = re.compile(r"(?<![A-Za-z0-9_.])-?\$?\d[\d,]*(?:\.\d+)?%?")
SYSTEM = (
    "You are a manufacturing planning analyst. Explain why the proposed action should or should not be taken, "
    "using ONLY the evidence provided. Every claim must cite the evidence ids it relies on. Do not invent numbers, "
    "orders, parts or suppliers; copy figures exactly as they appear in the cited evidence. Mention trade-offs "
    "(cost, harmed orders, irreversible supplier notifications). Evidence labelled 基线 describes the situation BEFORE the action. Answer in Chinese."
)
SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["summary", "reasoning", "risks"],
    "properties": {
        "summary": {"type": "string"},
        "reasoning": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["claim", "evidence_ids"],
                                                  "properties": {"claim": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}},
        "risks": {"type": "array", "items": {"type": "object", "additionalProperties": False, "required": ["claim", "evidence_ids"],
                                              "properties": {"claim": {"type": "string"}, "evidence_ids": {"type": "array", "items": {"type": "string"}}}}},
    },
}


class ExplanationError(RuntimeError):
    def __init__(self, code: str, message: str, status: int = 502):
        super().__init__(message)
        self.code, self.status = code, status


def build_evidence(outputs: dict, candidate: dict) -> list[dict]:
    totals = outputs.get("totals") or {}
    evidence = []

    def add(label, value):
        evidence.append({"id": f"E{len(evidence) + 1}", "label": label, "value": value})

    add("货币单位", "USD（金额均为美元）")
    add("基线季度预计收入", totals.get("projected_revenue"))
    add("季度收入目标（全部按期交付）", totals.get("revenue_target"))
    add("基线 OTIF", totals.get("otif"))
    add("基线有风险的需求事件数", totals.get("at_risk"))
    add("候选动作", candidate["label"])
    add("执行后季度预计收入", candidate.get("quarter_revenue_after"))
    add("季度收入变化", candidate.get("quarter_revenue_delta"))
    add("执行后 OTIF", candidate.get("otif_after"))
    add("执行后有风险的需求事件数", candidate.get("at_risk_after"))
    add("增量成本（加急费/返工/采购）", candidate.get("incremental_cost"))
    add("由风险转为按期的订单", [row["demand_id"] for row in candidate.get("protected_orders", [])])
    add("由按期转为有风险的订单", [row["demand_id"] for row in candidate.get("harmed_orders", [])])
    if candidate.get("capital_deferred") is not None:
        add("推迟到货而延后支付的采购金额", candidate["capital_deferred"])
    for action in candidate.get("actions", [])[:12]:
        add(f"动作 {action['action_type']}", {k: v for k, v in action.items() if k != "action_type"})
    involved = {candidate.get("item")} | {a.get("item") for a in candidate.get("actions", [])}
    for row in (outputs.get("coverage") or {}).get("rows", []):
        if row["item"] in involved or (row.get("critical") and candidate.get("product_line") in row.get("lines", [])):
            add(f"物料 {row['name']}（{row['item']}）基线覆盖（未执行本动作时，全年计划期）", {"on_hand": row["on_hand"], "first_shortage_week": row["first_shortage_week"],
                                                       "lowest_projected_balance": min(row["projected"]), "planned_orders": len(row["planned_orders"])})
    if candidate.get("hard_constraints"):
        add("未满足的硬约束", candidate["hard_constraints"])
    return evidence


def _numbers(value) -> list[float]:
    found = []
    text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
    for token in NUMBER.findall(text):
        clean = token.replace("$", "").replace(",", "").rstrip("%")
        try:
            found.append(float(clean))
        except ValueError:
            continue
    return found


def verify(answer: dict, evidence: list[dict]) -> dict:
    """Check citations and figures against the evidence, independently of the model."""
    by_id = {row["id"]: row for row in evidence}
    issues = []
    for section in ("reasoning", "risks"):
        for index, item in enumerate(answer.get(section, [])):
            unknown = [ref for ref in item["evidence_ids"] if ref not in by_id]
            if unknown:
                issues.append({"where": f"{section}[{index}]", "problem": "unknown_evidence", "ids": unknown})
                continue
            if not item["evidence_ids"]:
                issues.append({"where": f"{section}[{index}]", "problem": "no_citation"})
                continue
            allowed = [n for ref in item["evidence_ids"] for n in _numbers(by_id[ref]["value"])]
            for number in _numbers(re.sub(r"E\d+", "", item["claim"])):
                if abs(number) <= 12 and float(number).is_integer():
                    continue  # small counts/ordinals ("2 个 PO", "第 3 周") are checked via the cited ids
                ok = any(abs(number - a) <= max(0.5, abs(a) * 0.005) or abs(number - a * 100) <= 0.6 or abs(number * 1000 - a) <= abs(a) * 0.01
                         or abs(number * 10000 - a) <= abs(a) * 0.01 for a in allowed)
                if not ok:
                    issues.append({"where": f"{section}[{index}]", "problem": "unsupported_number", "value": number})
    return {"verified": not issues, "issues": issues}


def explain(outputs: dict, candidate: dict, *, client: httpx.Client | None = None) -> dict:
    key = os.environ.get("OPENAI_API_KEY", "").strip()
    if not key:
        raise ExplanationError("llm_unavailable", "OPENAI_API_KEY is not configured; no explanation was generated", 503)
    model = os.environ.get("EXPLANATION_MODEL", DEFAULT_MODEL)
    base = os.environ.get("OPENAI_BASE_URL", "https://api.openai.com/v1").rstrip("/")
    evidence = build_evidence(outputs, candidate)
    user = json.dumps({"action": candidate["label"], "evidence": evidence}, ensure_ascii=False)
    body = {"model": model, "temperature": 0.2, "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": user}],
            "response_format": {"type": "json_schema", "json_schema": {"name": "explanation", "strict": True, "schema": SCHEMA}}}
    http = client or httpx.Client(timeout=90)
    try:
        response = http.post(f"{base}/chat/completions", json=body, headers={"Authorization": f"Bearer {key}"})
    except httpx.HTTPError as exc:
        raise ExplanationError("llm_unavailable", f"Model call failed: {type(exc).__name__}") from exc
    if response.status_code != 200:
        raise ExplanationError("llm_error", f"Model returned HTTP {response.status_code}")
    payload = response.json()
    answer = json.loads(payload["choices"][0]["message"]["content"])
    check = verify(answer, evidence)
    return {"schema": "explanation/v1", "candidate_id": candidate["id"], "model": payload.get("model", model),
            "prompt_digest": sha256((SYSTEM + user).encode()).hexdigest(), "evidence": evidence, "answer": answer, **check,
            "usage": payload.get("usage")}
