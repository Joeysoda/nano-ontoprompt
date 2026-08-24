"""Small FalkorDB read service used by the industrial demo Graph page.

The Nano application still contains legacy Neo4j-powered advanced graph
features.  This service deliberately owns only the demonstrator path:
bounded instance sampling, quality summaries, and temporal COVERS history.
The graph name is derived from the Nano ontology id so two demos cannot read
each other's instances.
"""
from __future__ import annotations

import re
from collections import Counter
from typing import Any

try:
    from falkordb import FalkorDB
except ImportError:  # pragma: no cover - exercised in the container image
    FalkorDB = None  # type: ignore


def graph_name_for_ontology(ontology_id: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_]", "_", ontology_id).strip("_") or "default"
    return f"nano_{safe[:90]}"


class FalkorDBService:
    def __init__(self, host: str | None = None, port: int | None = None):
        from app.config import settings

        self.host = host or settings.falkordb_host
        self.port = int(port or settings.falkordb_port)
        self._db = None
        self._available = False
        if FalkorDB is None:
            return
        try:
            self._db = FalkorDB(host=self.host, port=self.port)
            self._db.select_graph("nano_healthcheck").query("RETURN 1")
            self._available = True
        except Exception:
            self._db = None

    @property
    def available(self) -> bool:
        return self._available

    def _graph(self, ontology_id: str):
        if not self._db:
            raise RuntimeError("FalkorDB unavailable")
        return self._db.select_graph(graph_name_for_ontology(ontology_id))

    @staticmethod
    def _node(node: Any) -> dict[str, Any]:
        props = dict(getattr(node, "properties", {}) or {})
        instance_id = props.get("_instance_id")
        labels = list(getattr(node, "labels", []) or [])
        return {
            "id": instance_id,
            "labels": labels or [props.get("_type") or "Entity"],
            "properties": {k: v for k, v in props.items() if not str(k).startswith("_")},
            "entity_type": props.get("_type") or (labels[0] if labels else "Entity"),
            "event_seq": props.get("event_seq"),
            "event_time": props.get("event_time"),
            "node_kind": "instance",
        }

    def get_graph_data(
        self,
        ontology_id: str,
        limit: int = 200,
        entity_type: str | None = None,
        seq_from: int | None = None,
        seq_to: int | None = None,
        relation_state: str = "all",
    ) -> dict[str, Any]:
        if not self.available:
            return {"nodes": [], "edges": [], "total_instances": 0, "graph_backend": "falkordb", "available": False}
        limit = max(1, min(int(limit), 1000))
        graph = self._graph(ontology_id)
        clauses = ["n._instance_id IS NOT NULL"]
        params: dict[str, Any] = {"limit": limit}
        if entity_type:
            clauses.append("n._type = $entity_type")
            params["entity_type"] = entity_type
        if seq_from is not None:
            clauses.append("n.event_seq >= $seq_from")
            params["seq_from"] = int(seq_from)
        if seq_to is not None:
            clauses.append("n.event_seq <= $seq_to")
            params["seq_to"] = int(seq_to)
        result = graph.query(
            f"MATCH (n) WHERE {' AND '.join(clauses)} RETURN n ORDER BY n.event_seq LIMIT $limit",
            params=params,
        )
        nodes: list[dict[str, Any]] = []
        ids: list[str] = []
        for row in result.result_set:
            node = self._node(row[0])
            if not node["id"]:
                continue
            nodes.append(node)
            ids.append(node["id"])

        edges: list[dict[str, Any]] = []
        if ids:
            rel_where = ["source._instance_id IN $ids", "target._instance_id IN $ids"]
            if relation_state == "current":
                rel_where.append("relation.valid_to IS NULL")
            rel_result = graph.query(
                "MATCH (source)-[relation]->(target) "
                f"WHERE {' AND '.join(rel_where)} "
                "RETURN source._instance_id, target._instance_id, type(relation), relation",
                params={"ids": ids},
            )
            for source_id, target_id, relation_type, relation in rel_result.result_set:
                props = dict(getattr(relation, "properties", {}) or {})
                edges.append({
                    "id": f"{source_id}:{relation_type}:{target_id}:{props.get('valid_from', '')}",
                    "source": source_id,
                    "target": target_id,
                    "type": relation_type,
                    "label": relation_type,
                    "properties": props,
                    "valid_from": props.get("valid_from"),
                    "valid_to": props.get("valid_to"),
                    "edge_kind": "instance",
                })
        total = graph.query("MATCH (n) WHERE n._instance_id IS NOT NULL RETURN count(n)")
        total_instances = int(total.result_set[0][0]) if total.result_set else 0
        return {
            "nodes": nodes,
            "edges": edges,
            "total_instances": total_instances,
            "sample_limit": limit,
            "graph_backend": "falkordb",
            "available": True,
            "time_kind": "ordinal" if any(n.get("event_seq") is not None for n in nodes) else "event_time",
        }

    def quality(self, ontology_id: str) -> dict[str, Any]:
        data = self.get_graph_data(ontology_id, limit=1000)
        nodes = data["nodes"]
        edges = data["edges"]
        ids = {n["id"] for n in nodes}
        connected = {e["source"] for e in edges} | {e["target"] for e in edges}
        isolated = [node_id for node_id in ids if node_id not in connected]
        type_counts = Counter(n.get("entity_type") or "Entity" for n in nodes)
        relation_counts = Counter(e.get("type") or "RELATED" for e in edges)
        score = 1.0
        if nodes:
            score -= min(0.4, len(isolated) / len(nodes) * 0.4)
        return {
            "ontology_id": ontology_id,
            "graph_backend": "falkordb",
            "available": data.get("available", False),
            "node_count": len(nodes),
            "edge_count": len(edges),
            "isolated_node_count": len(isolated),
            "orphan_relation_count": sum(1 for edge in edges if edge["source"] not in ids or edge["target"] not in ids),
            "object_type_counts": dict(type_counts),
            "relation_type_counts": dict(relation_counts),
            "quality_score": round(max(0.0, score), 4),
            "samples": {"isolated_node_ids": isolated[:10]},
        }

    def coverage(self, ontology_id: str, production_line_id: str) -> dict[str, Any]:
        if not self.available:
            return {"graph_backend": "falkordb", "available": False, "current": [], "history": []}
        graph = self._graph(ontology_id)
        current = graph.query(
            "MATCH (e)-[r:COVERS]->(l {_instance_id: $line_id}) "
            "WHERE r.valid_to IS NULL RETURN e._instance_id, r.valid_from ORDER BY r.valid_from",
            params={"line_id": production_line_id},
        )
        history = graph.query(
            "MATCH (e)-[r:COVERS]->(l {_instance_id: $line_id}) "
            "RETURN e._instance_id, r.valid_from, r.valid_to ORDER BY r.valid_from",
            params={"line_id": production_line_id},
        )
        return {
            "graph_backend": "falkordb",
            "available": True,
            "production_line_id": production_line_id,
            "current": [{"equipment_id": row[0], "valid_from": row[1]} for row in current.result_set],
            "history": [{"equipment_id": row[0], "valid_from": row[1], "valid_to": row[2]} for row in history.result_set],
        }
