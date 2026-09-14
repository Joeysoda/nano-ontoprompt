"""FactoryNet What-If compiler and isolated rule-scenario runner.

The runner operates on a JSON context copied from the selected observation and
never writes to FalkorDB or the published ontology.  It uses a deliberately
bounded, Semantica-compatible forward-chaining representation; when the
optional Semantica package is installed the engine marker is retained so the
serialized result records exactly which implementation was used.
"""
from __future__ import annotations

import copy
import hashlib
import json
import re
import uuid
from datetime import datetime, timezone
from typing import Any

from app.models.entity import Entity
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.dynamic_ontology import WhatIfRun, WhatIfScenario
from app.services.v2.graph.falkordb_service import FalkorDBService

ENGINE_COMMIT = "3a69721abf72d7188a0d6fd72c8462261b2c44eb"
MAX_NODES = 500
MAX_FACTS = 2000
MAX_RULES = 50
MAX_PREMISES = 4
MAX_ITERATIONS = 50
SAFE_TOKEN = re.compile(r"[^A-Za-z0-9_:./#-]+")


class WhatIfError(ValueError):
    pass


class WhatIfCancelled(WhatIfError):
    """Cooperative cancellation raised between bounded reasoning stages."""

    pass


def _token(value: Any) -> str:
    text = SAFE_TOKEN.sub("_", str(value or "")).strip("_")
    return text or "unknown"


def _term(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    if value is None:
        return "null"
    if isinstance(value, (int, float)):
        return str(value)
    return _token(value)


def _fact(predicate: Any, args: list[Any] | tuple[Any, ...]) -> tuple[str, tuple[str, ...]]:
    return _token(predicate).upper(), tuple(str(item) for item in args)


def _fact_text(fact: tuple[str, tuple[str, ...]]) -> str:
    return f"{fact[0]}({', '.join(fact[1])})"


def _is_var(value: str) -> bool:
    return str(value).startswith("?")


def _condition_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        for key in ("all", "conditions", "items", "and"):
            if isinstance(value.get(key), list):
                return [item for item in value[key] if isinstance(item, dict)]
        return [value]
    return [item for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _as_atom(item: dict[str, Any]) -> tuple[str, list[str]] | None:
    kind = str(item.get("kind") or item.get("type") or "relationship").lower()
    if kind in {"relationship", "relation", "edge"} or item.get("predicate") or item.get("relation"):
        predicate = item.get("predicate") or item.get("relation") or item.get("type")
        source = item.get("source") or item.get("source_var") or item.get("from") or "?source"
        target = item.get("target") or item.get("target_var") or item.get("to") or "?target"
        args = item.get("args") if isinstance(item.get("args"), list) else [source, target]
        return _token(predicate).upper(), [str(arg) if _is_var(str(arg)) else _term(arg) for arg in args]
    return None


def _compile_rule(rule: dict[str, Any], rule_id: str) -> dict[str, Any] | None:
    conditions = _condition_items(rule.get("condition") or rule.get("conditions") or rule.get("condition_json"))
    atoms: list[tuple[str, list[str]]] = []
    property_specs: list[dict[str, Any]] = []
    for item in conditions:
        # Property comparisons are compiled into deterministic unary atoms.
        if str(item.get("kind") or item.get("type") or "").lower() in {"property", "attribute"} or item.get("property_id") or item.get("property"):
            prop = item.get("property_id") or item.get("property")
            operator = str(item.get("operator") or "=")
            value = item.get("value") if "value" in item else item.get("comparison_value")
            predicate = f"PROP_{_token(prop)}_{_token(operator)}_{_term(value)}"
            entity_var = item.get("entity_var") or item.get("instance") or item.get("source") or "?source"
            atoms.append((predicate.upper(), [str(entity_var) if _is_var(str(entity_var)) else _term(entity_var)]))
            property_specs.append({"predicate": predicate.upper(), "property": str(prop), "operator": operator, "value": value, "entity_var": str(entity_var)})
            continue
        atom = _as_atom(item)
        if atom:
            atoms.append(atom)
    effect = rule.get("effect") or rule.get("result") or {}
    if isinstance(effect, list):
        effect = effect[0] if effect else {}
    conclusion = _as_atom(effect if isinstance(effect, dict) else {})
    if not conclusion or not atoms:
        return None
    if len(atoms) > MAX_PREMISES:
        raise WhatIfError(f"规则 {rule_id} 前提超过 {MAX_PREMISES} 个")
    bound = {term for _, args in atoms for term in args if _is_var(term)}
    if any(_is_var(term) and term not in bound for term in conclusion[1]):
        raise WhatIfError(f"规则 {rule_id} 的结论包含未绑定变量")
    return {"id": rule_id, "conditions": atoms, "conclusion": conclusion, "property_specs": property_specs}


def _match_condition(condition: tuple[str, list[str]], fact: tuple[str, tuple[str, ...]], bindings: dict[str, str]) -> dict[str, str] | None:
    predicate, terms = condition
    if fact[0] != predicate or len(terms) != len(fact[1]):
        return None
    next_bindings = dict(bindings)
    for expected, actual in zip(terms, fact[1]):
        if _is_var(expected):
            if expected in next_bindings and next_bindings[expected] != actual:
                return None
            next_bindings[expected] = actual
        elif expected != actual:
            return None
    return next_bindings


def _parse_fact_text(value: Any) -> tuple[str, tuple[str, ...]] | None:
    """Parse the deliberately small atom format returned by Semantica."""
    match = re.fullmatch(r"\s*([A-Za-z_][A-Za-z0-9_]*)\((.*)\)\s*", str(value or ""))
    if not match:
        return None
    inside = match.group(2).strip()
    args = tuple(part.strip() for part in inside.split(",")) if inside else ()
    return match.group(1).upper(), args


def _semantica_infer(
    facts: list[tuple[str, tuple[str, ...]]], rules: list[dict[str, Any]]
) -> tuple[set[tuple[str, tuple[str, ...]]], dict[tuple[str, tuple[str, ...]], dict[str, Any]]] | None:
    """Run the pinned Semantica engine and keep one grounded proof per result.

    The What-If runner still has a deterministic compatibility implementation
    for installations where the optional package is unavailable.  When the
    package is present, however, the actual result set—not just a health
    probe—is used for the scenario diff.  Rule objects retain our stable rule
    IDs so the UI can link a proof back to the saved scenario rule.
    """
    try:
        from semantica.reasoning import Reasoner
        from semantica.reasoning.reasoner import Rule

        semantica_rules = []
        for item in rules:
            conditions = [
                f"{predicate}({', '.join(terms)})"
                for predicate, terms in item["conditions"]
            ]
            predicate, terms = item["conclusion"]
            semantica_rules.append(
                Rule(
                    rule_id=str(item["id"]),
                    name=str(item["id"]),
                    conditions=conditions,
                    conclusion=f"{predicate}({', '.join(terms)})",
                )
            )
        results = Reasoner(max_iterations=MAX_ITERATIONS).infer_with_results(
            [_fact_text(item) for item in facts], semantica_rules
        )
        known = set(facts)
        proofs: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
        for result in results:
            conclusion = _parse_fact_text(getattr(result, "conclusion", ""))
            if conclusion is None or conclusion in known:
                continue
            rule_used = getattr(result, "rule_used", None)
            premises = [str(item) for item in (getattr(result, "premises", None) or [])]
            proofs[conclusion] = {
                "fact": _fact_text(conclusion),
                "rule_id": str(getattr(rule_used, "rule_id", "") or "unknown"),
                "premises": premises,
                "bindings": {},
                "proof_scope": "one_grounded_derivation",
            }
            known.add(conclusion)
        return known, proofs
    except Exception:
        return None


def infer_facts(facts: list[tuple[str, tuple[str, ...]]], rules: list[dict[str, Any]]) -> dict[str, Any]:
    if len(facts) > MAX_FACTS:
        raise WhatIfError(f"事实超过 {MAX_FACTS} 条限制")
    if len(rules) > MAX_RULES:
        raise WhatIfError(f"规则超过 {MAX_RULES} 条限制")
    semantica_result = _semantica_infer(facts, rules) if rules else None
    if semantica_result is not None:
        known, proofs = semantica_result
        return {
            "facts": [_fact_text(item) for item in sorted(known)],
            "derived": [proofs[item] for item in sorted(proofs)],
            "iterations": 1,
            "engine": "semantica",
        }
    known = set(facts)
    proofs: dict[tuple[str, tuple[str, ...]], dict[str, Any]] = {}
    for iteration in range(MAX_ITERATIONS):
        added = 0
        for rule in rules:
            matches = [({}, [])]
            for condition in rule["conditions"]:
                next_matches = []
                for bindings, premises in matches:
                    for fact in list(known):
                        result = _match_condition(condition, fact, bindings)
                        if result is not None:
                            next_matches.append((result, premises + [fact]))
                matches = next_matches
                if not matches:
                    break
            for bindings, premises in matches:
                predicate, terms = rule["conclusion"]
                grounded = tuple(bindings.get(term, term) if _is_var(term) else term for term in terms)
                conclusion = (predicate, grounded)
                if conclusion not in known:
                    known.add(conclusion); added += 1
                    proofs[conclusion] = {"fact": _fact_text(conclusion), "rule_id": rule["id"], "premises": [_fact_text(item) for item in premises], "bindings": bindings, "proof_scope": "one_grounded_derivation"}
                    if len(known) > MAX_FACTS:
                        raise WhatIfError(f"推演事实超过 {MAX_FACTS} 条限制")
        if not added:
            return {"facts": [_fact_text(item) for item in sorted(known)], "derived": [proofs[item] for item in sorted(proofs)], "iterations": iteration + 1, "engine": "deterministic-compatible"}
    raise WhatIfError(f"推演超过 {MAX_ITERATIONS} 次迭代限制，未收敛")


def _context_facts(
    context: dict[str, Any],
    *,
    rules: list[dict[str, Any]] | None = None,
    assumptions: list[dict[str, Any]] | None = None,
) -> tuple[list[tuple[str, tuple[str, ...]]], dict[str, list[dict[str, Any]]]]:
    """Compile only facts that the selected scenario can actually use.

    A FactoryNet observation carries dozens of sensor columns.  Copying every
    scalar column into a symbolic fact multiplies a two-hop neighbourhood into
    tens of thousands of facts even though the rules normally use only one or
    two fields.  The context JSON remains intact for the inspector, while the
    reasoning input is reduced deterministically to:

    * all bounded relationship edges;
    * type atoms only when a rule explicitly asks for a ``TYPE_*`` predicate;
    * properties referenced by a rule comparison or an explicit scenario
      property override.

    This is a semantic projection, not an arbitrary truncation.  The hard
    ``MAX_FACTS`` guard in ``infer_facts`` still rejects a genuinely oversized
    structural context.
    """
    facts: list[tuple[str, tuple[str, ...]]] = []
    evidence: dict[str, list[dict[str, Any]]] = {}
    executable_rules = rules or []
    required_predicates = {
        str(predicate).upper()
        for rule in executable_rules
        for predicate, _terms in rule.get("conditions", [])
    }
    required_type_predicates = {
        predicate for predicate in required_predicates if predicate.startswith("TYPE_")
    }
    required_properties = {
        str(spec.get("property"))
        for rule in executable_rules
        for spec in rule.get("property_specs", [])
        if spec.get("property")
    }
    for item in assumptions or []:
        kind = str(item.get("kind") or item.get("type") or "").lower()
        if kind in {"set_property", "override_property", "property"}:
            property_name = item.get("property") or item.get("property_id")
            if property_name:
                required_properties.add(str(property_name))
    nodes = context.get("nodes") or []
    edges = context.get("edges") or []
    for node in nodes:
        node_id = str(node.get("id") or "")
        if not node_id:
            continue
        type_fact = _fact(f"TYPE_{node.get('entity_type') or 'Entity'}", [node_id])
        if not required_type_predicates or type_fact[0] in required_type_predicates:
            # Do not add all type atoms by default: no current FactoryNet rule
            # consumes them, and the static episode hub would otherwise spend
            # most of the fact budget before relationship inference starts.
            if required_type_predicates:
                facts.append(type_fact)
                evidence.setdefault(_fact_text(type_fact), []).extend(node.get("evidence") or [])
        for key, value in (node.get("properties") or {}).items():
            if key not in required_properties or isinstance(value, (dict, list)) or value in (None, ""):
                continue
            prop_name = f"PROP_{_token(key)}"
            prop_fact = _fact(prop_name, [node_id, _term(value)])
            facts.append(prop_fact)
            # Equality predicates are useful for the built-in property rule
            # compiler and remain deterministic; non-equality is evaluated by
            # the scenario compiler before facts are sent to the engine.
            evidence.setdefault(_fact_text(prop_fact), []).extend(node.get("evidence") or [])
    for edge in edges:
        source, target = str(edge.get("source") or ""), str(edge.get("target") or "")
        if not source or not target:
            continue
        fact = _fact(edge.get("type") or edge.get("label") or "RELATED", [source, target])
        facts.append(fact); evidence.setdefault(_fact_text(fact), []).extend(edge.get("evidence") or [])
    return list(dict.fromkeys(facts)), evidence


def _compare(value: Any, operator: str, expected: Any) -> bool:
    try:
        if operator in {"=", "=="}: return value == expected or str(value) == str(expected)
        if operator in {"≠", "!="}: return value != expected and str(value) != str(expected)
        if operator in {">", "≥", ">=", "<", "≤", "<="}:
            left, right = float(value), float(expected)
            return {">": left > right, "≥": left >= right, ">=": left >= right, "<": left < right, "≤": left <= right, "<=": left <= right}[operator]
        if operator in {"属于", "in"}:
            choices = expected if isinstance(expected, (list, tuple, set)) else [expected]
            return value in choices or str(value) in {str(item) for item in choices}
    except (TypeError, ValueError):
        return False
    return False


def _augment_property_facts(facts: list[tuple[str, tuple[str, ...]]], context: dict[str, Any], rules: list[dict[str, Any]]) -> list[tuple[str, tuple[str, ...]]]:
    augmented = list(facts)
    for rule in rules:
        for spec in rule.get("property_specs", []):
            for node in context.get("nodes") or []:
                properties = node.get("properties") or {}
                if spec["property"] in properties and _compare(properties.get(spec["property"]), spec["operator"], spec.get("value")):
                    augmented.append((spec["predicate"], (str(node.get("id")),)))
    return list(dict.fromkeys(augmented))


def _apply_assumptions(context: dict[str, Any], assumptions: list[dict[str, Any]]) -> dict[str, Any]:
    result = copy.deepcopy(context)
    nodes = {str(node.get("id")): node for node in result.get("nodes") or [] if node.get("id")}
    edges = list(result.get("edges") or [])
    for item in assumptions:
        kind = str(item.get("kind") or item.get("type") or "").lower()
        if kind in {"set_property", "override_property", "property"}:
            node = nodes.get(str(item.get("instance_id") or item.get("node_id") or ""))
            if not node:
                raise WhatIfError(f"情景属性覆盖目标不存在：{item.get('instance_id') or item.get('node_id')}")
            property_name = str(item.get("property") or item.get("property_id"))
            value = item.get("value")
            node.setdefault("properties", {})[property_name] = value
            # FactoryNet stores tool condition as a synchronized relationship
            # to a ToolCondition instance.  Keep the relationship projection
            # consistent with the overridden scalar so the demo rule produces
            # a meaningful added/removed conclusion.
            if property_name in {"ctx_tool_condition", "tool_condition"}:
                source_id = str(node.get("id"))
                edges = [edge for edge in edges if not (str(edge.get("source")) == source_id and str(edge.get("type")) == "HAS_TOOL_CONDITION")]
                target_id = next((candidate_id for candidate_id, candidate in nodes.items() if str(candidate.get("entity_type")) == "ToolCondition" and str((candidate.get("properties") or {}).get("value")) == str(value)), None)
                if not target_id:
                    target_id = f"scenario:ToolCondition:{_token(value)}"
                    nodes[target_id] = {"id": target_id, "entity_type": "ToolCondition", "properties": {"value": value}, "evidence": [{"source": "情景假设"}]}
                edges.append({"id": f"scenario:{source_id}:HAS_TOOL_CONDITION:{target_id}", "source": source_id, "target": target_id, "type": "HAS_TOOL_CONDITION", "properties": {"source": "情景假设"}, "evidence": [{"source": "情景假设"}]})
        elif kind in {"add_relation", "add_relationship"}:
            source, target = str(item.get("source") or ""), str(item.get("target") or "")
            if source not in nodes or target not in nodes:
                raise WhatIfError("情景新增关系的端点不在两跳范围内")
            edges.append({"id": f"scenario:{uuid.uuid4().hex}", "source": source, "target": target, "type": str(item.get("type") or item.get("predicate") or "RELATED"), "properties": {"source": "情景假设"}, "evidence": [{"source": "情景假设"}]})
        elif kind in {"remove_relation", "remove_relationship"}:
            source, target, predicate = str(item.get("source") or ""), str(item.get("target") or ""), str(item.get("type") or item.get("predicate") or "")
            edges = [edge for edge in edges if not (str(edge.get("source")) == source and str(edge.get("target")) == target and (not predicate or str(edge.get("type")) == predicate))]
        else:
            raise WhatIfError(f"不支持的情景假设：{kind or '空'}")
    result["nodes"] = list(nodes.values()); result["edges"] = edges
    return result


def _rules_for_context(
    db,
    ontology_id: str,
    rule_overrides: dict[str, Any] | None = None,
    *,
    snapshot: dict[str, Any] | None = None,
) -> list[dict[str, Any]]:
    overrides = rule_overrides or {}
    disabled = {str(item) for item in overrides.get("disabled", [])}
    enabled = {str(item) for item in overrides.get("enabled", [])}
    if disabled & enabled:
        raise WhatIfError("同一条规则不能同时启用和停用")
    if snapshot is not None:
        rows = [
            {
                "id": item.get("id"),
                "enabled": bool(item.get("enabled", True)),
                "condition": item.get("condition") or item.get("conditions") or {},
                "effect": item.get("effect") or item.get("result") or {},
            }
            for item in snapshot.get("logic_rules", [])
            if isinstance(item, dict)
        ]
    else:
        rows = db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all()
    compiled: list[dict[str, Any]] = []
    for row in rows:
        row_id = str(row.get("id") if isinstance(row, dict) else row.id)
        row_enabled = bool(row.get("enabled", True) if isinstance(row, dict) else row.enabled)
        if not row_enabled and row_id not in enabled:
            continue
        if row_id in disabled:
            continue
        try:
            condition = row.get("condition", {}) if isinstance(row, dict) else row.condition_json or {}
            effect = row.get("effect", {}) if isinstance(row, dict) else row.effect_json or {}
            rule = _compile_rule({"condition": condition, "effect": effect}, row_id)
        except WhatIfError:
            rule = None
        if rule:
            compiled.append(rule)
    temporary = overrides.get("temporary", []) or []
    if len(temporary) > MAX_RULES:
        raise WhatIfError(f"情景规则超过 {MAX_RULES} 条限制")
    for index, item in enumerate(temporary):
        rule = _compile_rule(item, str(item.get("id") or f"scenario-rule-{index + 1}"))
        if rule:
            compiled.append(rule)
    if len(compiled) > MAX_RULES:
        raise WhatIfError(f"可执行规则超过 {MAX_RULES} 条限制")
    return compiled


def _property_definitions(entity: Entity) -> list[dict[str, Any]]:
    raw = entity.properties or {}
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if isinstance(raw, dict):
        values = raw.get("property_definitions") or raw.get("properties")
        if isinstance(values, list):
            return [item for item in values if isinstance(item, dict)]
        return [
            {"id": str(key), "name": str(key), "type": "string"}
            for key in raw
            if key not in {"schema_version", "source_fields", "evidence", "color", "icon", "data_class"}
        ]
    return []


def _validate_assumptions(db, scenario: WhatIfScenario, context: dict[str, Any]) -> None:
    """Validate scenario edits before any reasoning engine is invoked."""
    nodes = {
        str(node.get("id")): node
        for node in context.get("nodes") or []
        if node.get("id")
    }
    entities = db.query(Entity).filter(Entity.ontology_id == scenario.ontology_id).all()
    entity_by_name: dict[str, Entity] = {}
    for entity in entities:
        for key in (entity.id, entity.canonical_id, entity.name_en, entity.name_cn, entity.type):
            if key:
                entity_by_name[str(key)] = entity
    for item in list(scenario.assumptions_json or []):
        kind = str(item.get("kind") or item.get("type") or "").lower()
        if kind in {"set_property", "override_property", "property"}:
            node_id = str(item.get("instance_id") or item.get("node_id") or "")
            node = nodes.get(node_id)
            if node is None:
                raise WhatIfError(f"情景属性覆盖目标不存在：{node_id or '空'}")
            property_name = str(item.get("property") or item.get("property_id") or "")
            if not property_name:
                raise WhatIfError("情景属性覆盖缺少属性标识")
            entity = entity_by_name.get(str(node.get("entity_type") or ""))
            definition = None
            if entity:
                definition = next(
                    (
                        prop for prop in _property_definitions(entity)
                        if str(prop.get("id") or prop.get("name")) == property_name
                    ),
                    None,
                )
            expected_type = str((definition or {}).get("type") or "").lower()
            value = item.get("value")
            if expected_type in {"integer", "int"} and (isinstance(value, bool) or not isinstance(value, int)):
                raise WhatIfError(f"属性 {property_name} 需要整数值")
            if expected_type in {"decimal", "float", "number"} and (isinstance(value, bool) or not isinstance(value, (int, float))):
                raise WhatIfError(f"属性 {property_name} 需要数值")
            if expected_type in {"boolean", "bool"} and not isinstance(value, bool):
                raise WhatIfError(f"属性 {property_name} 需要布尔值")
            enum_values = (definition or {}).get("enum") if definition else None
            if isinstance(enum_values, list) and value not in enum_values and str(value) not in {str(item) for item in enum_values}:
                raise WhatIfError(f"属性 {property_name} 的值不在枚举范围内")
        elif kind in {"add_relation", "add_relationship", "remove_relation", "remove_relationship"}:
            source = str(item.get("source") or "")
            target = str(item.get("target") or "")
            if source not in nodes or target not in nodes:
                raise WhatIfError("情景关系端点不在两跳范围内")
        else:
            raise WhatIfError(f"不支持的情景假设：{kind or '空'}")


def run_preview(
    db,
    scenario: WhatIfScenario,
    run: WhatIfRun,
    *,
    on_stage=None,
) -> dict[str, Any]:
    context = copy.deepcopy(scenario.baseline_json or {})
    if len(context.get("nodes") or []) > MAX_NODES:
        raise WhatIfError(f"推演节点超过 {MAX_NODES} 个限制")
    revision_snapshot = None
    if scenario.base_revision_id:
        revision = db.query(OntologyRevision).filter(
            OntologyRevision.id == scenario.base_revision_id,
            OntologyRevision.ontology_id == scenario.ontology_id,
        ).first()
        if not revision:
            raise WhatIfError("情景固定的本体修订不存在")
        revision_snapshot = revision.snapshot_json or {}
    rules = _rules_for_context(
        db,
        scenario.ontology_id,
        scenario.rule_overrides_json or {},
        snapshot=revision_snapshot,
    )
    assumptions = list(scenario.assumptions_json or [])
    facts, evidence = _context_facts(context, rules=rules, assumptions=assumptions)
    facts = _augment_property_facts(facts, context, rules)
    if len(facts) > MAX_FACTS:
        raise WhatIfError(
            f"当前选定范围包含 {len(facts)} 条可推理事实，超过 {MAX_FACTS} 条限制；"
            "请切换为“当前位置窗口”或降低 Ordinal 后重试"
        )
    baseline = infer_facts(facts, rules)
    if on_stage:
        on_stage("apply_assumption", 50)
    _validate_assumptions(db, scenario, context)
    changed = _apply_assumptions(context, assumptions)
    if on_stage:
        on_stage("scenario_reasoning", 65)
    scenario_facts, scenario_evidence = _context_facts(changed, rules=rules, assumptions=assumptions)
    scenario_facts = _augment_property_facts(scenario_facts, changed, rules)
    if len(scenario_facts) > MAX_FACTS:
        raise WhatIfError(
            f"应用情景后包含 {len(scenario_facts)} 条可推理事实，超过 {MAX_FACTS} 条限制；"
            "请缩小基线范围或切换为“当前位置窗口”"
        )
    scenario_result = infer_facts(scenario_facts, rules)
    base_set, scenario_set = set(baseline["facts"]), set(scenario_result["facts"])
    diff = {"added": sorted(scenario_set - base_set), "removed": sorted(base_set - scenario_set), "unchanged": sorted(base_set & scenario_set), "added_count": len(scenario_set - base_set), "removed_count": len(base_set - scenario_set), "proofs": scenario_result.get("derived", [])}
    for item in diff["proofs"]:
        item["evidence"] = [e for premise in item.get("premises", []) for e in scenario_evidence.get(premise, [])] or [{"source": "情景假设"}]
    if on_stage:
        on_stage("generate_diff", 85)
    engine = "semantica" if baseline.get("engine") == "semantica" and scenario_result.get("engine") == "semantica" else "deterministic-compatible (Semantica package unavailable or rejected input)"
    return {"baseline": baseline, "scenario": scenario_result, "diff": diff, "evidence": {**evidence, **scenario_evidence}, "engine": engine, "engine_version": ENGINE_COMMIT}


def build_context(db, ontology_id: str, *, target_instance_id: str | None = None, episode_id: str | None = None, at: str | None = None, mode: str = "cumulative") -> dict[str, Any]:
    project = db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first()
    if not project:
        raise WhatIfError("本体不存在")
    if project.data_class != "temporal" or not re.search(r"factorynet|factory|cnc", project.name or "", re.I):
        raise WhatIfError("What-If 第一版仅开放 FactoryNet 时序本体")
    if not target_instance_id:
        raise WhatIfError("请先在 FactoryNet 数据模型中选择一个 Observation，再开始推演")
    current_revision = None
    if project.current_revision_id:
        current_revision = db.query(OntologyRevision).filter(OntologyRevision.id == project.current_revision_id).first()
    service = FalkorDBService()
    if not service.available:
        raise WhatIfError("FalkorDB 不可用，无法读取选定观测及其两跳邻域")
    context = service.get_neighborhood(ontology_id, target_id=target_instance_id, episode_id=episode_id, seq_to=int(at) if at not in (None, "") and str(at).isdigit() else None, mode=mode, hops=2, limit=MAX_NODES)
    if not context.get("nodes"):
        raise WhatIfError("未找到选定观测，请先在数据模型中选择 Observation")
    selected = next(
        (node for node in context.get("nodes", []) if str(node.get("id")) == str(context.get("target_instance_id") or target_instance_id)),
        context.get("nodes", [None])[0],
    )
    if selected and "observation" not in str(selected.get("entity_type") or "").casefold():
        raise WhatIfError("What-If 基线必须是 Observation，请从数据模型选择观测实例")
    refs = {}
    from app.models.v2.construction import EvidenceRef
    for row in db.query(EvidenceRef).filter(EvidenceRef.ontology_id == ontology_id).all():
        refs.setdefault(row.assertion_id, []).append({"evidence_ref_id": row.id, "source_file": row.source_file, "source_row": row.source_row_id, "source_sample_id": row.source_sample_id, "content_hash": row.content_hash, "evidence_text": row.evidence_text})
    for node in context.get("nodes", []): node["evidence"] = refs.get(str(node.get("id")), [])
    for edge in context.get("edges", []): edge["evidence"] = refs.get(str(edge.get("id")), [])
    return {"ontology_id": ontology_id, "data_class": project.data_class, "base_revision_id": current_revision.id if current_revision else None, "dataset_version_id": context.get("dataset_version_id"), "target_instance_id": target_instance_id or context.get("target_instance_id"), "episode_id": episode_id, "at": at, "mode": mode, "nodes": context.get("nodes", []), "edges": context.get("edges", []), "rules": [{"id": rule.id, "name_cn": rule.name_cn, "enabled": bool(rule.enabled), "condition": rule.condition_json or {}, "effect": rule.effect_json or {}} for rule in db.query(LogicRule).filter(LogicRule.ontology_id == ontology_id).all()], "limits": {"hops": 2, "nodes": MAX_NODES, "facts": MAX_FACTS, "rules": MAX_RULES, "premises": MAX_PREMISES, "iterations": MAX_ITERATIONS}}


def serialize_scenario(item: WhatIfScenario) -> dict[str, Any]:
    return {"id": item.id, "ontology_id": item.ontology_id, "name": item.name, "status": item.status, "base_revision_id": item.base_revision_id, "dataset_version_id": item.dataset_version_id, "baseline": item.baseline_json or {}, "assumptions": item.assumptions_json or [], "rule_overrides": item.rule_overrides_json or {}, "created_at": item.created_at.isoformat() if item.created_at else None, "updated_at": item.updated_at.isoformat() if item.updated_at else None}


def serialize_run(item: WhatIfRun) -> dict[str, Any]:
    return {"id": item.id, "scenario_id": item.scenario_id, "ontology_id": item.ontology_id, "status": item.status, "stage": item.stage, "progress": item.progress, "engine": item.engine, "engine_version": item.engine_version, "context": item.context_json or {}, "baseline": item.baseline_result_json or {}, "scenario": item.scenario_result_json or {}, "diff": item.diff_json or {}, "input_hash": item.input_hash, "error": item.error, "cancel_requested": bool(item.cancel_requested), "created_at": item.created_at.isoformat() if item.created_at else None, "started_at": item.started_at.isoformat() if item.started_at else None, "completed_at": item.completed_at.isoformat() if item.completed_at else None}
