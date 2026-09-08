"""Small, deterministic gold comparison metrics for ontology experiments."""
from __future__ import annotations

from typing import Any, Iterable


def _prf(tp: int, predicted: int, gold: int) -> dict[str, float | int]:
    precision = tp / predicted if predicted else 0.0
    recall = tp / gold if gold else 0.0
    f1 = 2 * precision * recall / (precision + recall) if precision + recall else 0.0
    return {"true_positive": tp, "predicted": predicted, "gold": gold, "precision": round(precision, 6), "recall": round(recall, 6), "f1": round(f1, 6)}


def _key(item: Any) -> str:
    if isinstance(item, str):
        return item
    if isinstance(item, dict):
        return "|".join(str(item.get(k, "")) for k in ("subject", "predicate", "object", "id", "type", "text"))
    if isinstance(item, (list, tuple)):
        return "|".join(map(str, item))
    return str(item)


def compare_sets(predicted: Iterable[Any], gold: Iterable[Any]) -> dict[str, Any]:
    pred = {_key(item) for item in predicted}
    truth = {_key(item) for item in gold}
    return _prf(len(pred & truth), len(pred), len(truth))


def schema_compliance(predicted: Iterable[dict], schema: dict[str, Any]) -> dict[str, Any]:
    allowed_types = set(schema.get("entity_types") or schema.get("classes") or [])
    allowed_relations = set(schema.get("relation_types") or schema.get("relations") or [])
    nodes = list(predicted)
    violations: list[dict[str, Any]] = []
    checked = 0
    for node in nodes:
        node_type = node.get("type") or node.get("entity_type")
        if allowed_types:
            checked += 1
            if node_type not in allowed_types:
                violations.append({"kind": "entity_type", "value": node_type})
        relation = node.get("relation") or node.get("predicate")
        if relation and allowed_relations:
            checked += 1
            if relation not in allowed_relations:
                violations.append({"kind": "relation_type", "value": relation})
    return {"checked": checked, "violations": violations, "violation_count": len(violations), "compliance": round(1 - len(violations) / checked, 6) if checked else 1.0}


def evaluate(predicted_entities: list[Any], gold_entities: list[Any], predicted_triples: list[Any], gold_triples: list[Any], schema_nodes: list[dict] | None = None, schema: dict | None = None) -> dict[str, Any]:
    entity_metrics = compare_sets(predicted_entities, gold_entities)
    triple_metrics = compare_sets(predicted_triples, gold_triples)
    result: dict[str, Any] = {
        "entity": entity_metrics,
        "triple": triple_metrics,
        "hallucination_rate": round(max(0, len(set(map(_key, predicted_triples)) - set(map(_key, gold_triples)))) / len(predicted_triples), 6) if predicted_triples else 0.0,
        "duplicate_rate": round((len(predicted_triples) - len(set(map(_key, predicted_triples)))) / len(predicted_triples), 6) if predicted_triples else 0.0,
    }
    if schema is not None:
        result["schema"] = schema_compliance(schema_nodes or [], schema)
    return result
