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

    def _ensure_instance_index(self, ontology_id: str) -> None:
        """Create the Falkor range index used by idempotent MERGE writes.

        FalkorDB versions before 1.8 use ``CREATE INDEX ON :Label(prop)`` and
        reject ``IF NOT EXISTS``.  Index creation is therefore intentionally
        best-effort and safe to repeat for every construction run.
        """
        if not self.available:
            return
        try:
            self._graph(ontology_id).query("CREATE INDEX ON :Instance(_instance_id)")
        except Exception:
            pass

    @staticmethod
    def _safe_relation_type(value: str) -> str:
        relation = re.sub(r"[^A-Za-z0-9_]", "_", str(value or "RELATED")).upper()
        return relation if relation and relation[0].isalpha() else f"R_{relation}"

    def upsert_instances(self, ontology_id: str, instances: list[dict[str, Any]]) -> int:
        """Idempotently write ontology instances into the per-ontology graph."""
        if not self.available or not instances:
            return 0
        graph = self._graph(ontology_id)
        self._ensure_instance_index(ontology_id)
        # Older demo runs predate the Instance label.  Label them once before
        # the indexed MERGE so a retry does not create duplicates and later
        # writes use the range index instead of scanning every node.
        try:
            graph.query("MATCH (n) WHERE n._instance_id IS NOT NULL SET n:Instance")
        except Exception:
            pass
        rows = []
        for item in instances:
            instance_id = str(item.get("id") or item.get("_instance_id") or "")
            if not instance_id:
                continue
            props = dict(item.get("properties") or {})
            props.update({"_instance_id": instance_id, "_type": item.get("entity_type") or item.get("type") or "Entity", "_ontology_id": ontology_id})
            rows.append({"id": instance_id, "props": props})
        if not rows:
            return 0
        graph.query(
            "UNWIND $rows AS row "
            "MERGE (n:Instance {_instance_id: row.id}) "
            "SET n:Instance, n += row.props",
            params={"rows": rows},
        )
        return len(rows)

    def upsert_relations(self, ontology_id: str, relations: list[dict[str, Any]]) -> int:
        """Write relationship instances with stable source/type/target identity."""
        if not self.available or not relations:
            return 0
        graph = self._graph(ontology_id)
        grouped: dict[str, list[dict[str, Any]]] = {}
        for relation in relations:
            source = str(relation.get("source") or "")
            target = str(relation.get("target") or "")
            if not source or not target:
                continue
            rel_type = self._safe_relation_type(relation.get("type") or "RELATED")
            props = dict(relation.get("properties") or {})
            props.setdefault("_ontology_id", ontology_id)
            grouped.setdefault(rel_type, []).append({"source": source, "target": target, "props": props})
        written = 0
        for rel_type, rows in grouped.items():
            graph.query(
                "UNWIND $rows AS row "
                "MATCH (a:Instance {_instance_id: row.source}), (b:Instance {_instance_id: row.target}) "
                f"MERGE (a)-[r:{rel_type}]->(b) SET r += row.props",
                params={"rows": rows},
            )
            written += len(rows)
        return written

    def get_temporal_relations(
        self,
        ontology_id: str,
        relation_type: str | None = None,
        subject_id: str | None = None,
        object_id: str | None = None,
        event_seq: int | None = None,
        relation_state: str = "all",
        limit: int = 200,
    ) -> dict[str, Any]:
        """Return bounded, parameterized relationship assertions.

        Relationship labels are sanitized before being interpolated because
        FalkorDB/Cypher does not support parameters in a type position. All
        user values remain query parameters, and every node is constrained to
        the per-ontology graph selected by :meth:`_graph`.
        """
        if not self.available:
            return {"available": False, "graph_backend": "falkordb", "relations": []}
        graph = self._graph(ontology_id)
        limit = max(1, min(int(limit), 1000))
        rel_pattern = f"[r:{self._safe_relation_type(relation_type)}]" if relation_type else "[r]"
        clauses = ["a._instance_id IS NOT NULL", "b._instance_id IS NOT NULL"]
        params: dict[str, Any] = {"limit": limit}
        if subject_id:
            clauses.append("a._instance_id = $subject_id")
            params["subject_id"] = subject_id
        if object_id:
            clauses.append("b._instance_id = $object_id")
            params["object_id"] = object_id
        if event_seq is not None:
            clauses.append("r.event_seq = $event_seq")
            params["event_seq"] = int(event_seq)
        if relation_state == "current":
            clauses.append("r.valid_to IS NULL")
        result = graph.query(
            f"MATCH (a)-{rel_pattern}->(b) WHERE {' AND '.join(clauses)} "
            "RETURN a._instance_id, b._instance_id, type(r), r LIMIT $limit",
            params=params,
        )
        relations: list[dict[str, Any]] = []
        for source_id, target_id, rel_name, rel in result.result_set:
            props = dict(getattr(rel, "properties", {}) or {})
            relations.append({
                "id": f"{source_id}:{rel_name}:{target_id}:{props.get('valid_from', '')}",
                "source": source_id,
                "target": target_id,
                "type": rel_name,
                "properties": props,
                "event_seq": props.get("event_seq"),
                "event_time": props.get("event_time"),
                "valid_from": props.get("valid_from"),
                "valid_to": props.get("valid_to"),
            })
        return {
            "available": True,
            "graph_backend": "falkordb",
            "ontology_id": ontology_id,
            "relation_type": relation_type,
            "relation_state": relation_state,
            "relations": relations,
            "count": len(relations),
            "sample_limit": limit,
        }

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

    def _event_nodes(self, ontology_id: str, at: str | None = None, limit: int = 1000) -> list[dict[str, Any]]:
        """Read a bounded temporal snapshot from the ontology-isolated graph."""
        if not self.available:
            return []
        graph = self._graph(ontology_id)
        bounded = max(1, min(int(limit), 1000))
        # Static ontology nodes (Building/Point) are always part of a
        # snapshot. Fill the remaining budget with the newest observations so
        # an early and a late snapshot visibly differ even when the dataset
        # contains tens of thousands of events.
        static_result = graph.query(
            "MATCH (n) WHERE n._instance_id IS NOT NULL AND n.event_time IS NULL RETURN n",
        )
        static_nodes = [self._node(row[0]) for row in static_result.result_set if self._node(row[0]).get("id")]
        event_limit = max(0, bounded - len(static_nodes))
        if event_limit == 0:
            return static_nodes[:bounded]
        clauses = ["n._instance_id IS NOT NULL", "n.event_time IS NOT NULL"]
        params: dict[str, Any] = {"limit": event_limit}
        if at:
            clauses.append("n.event_time <= $at")
            params["at"] = at
        result = graph.query(f"MATCH (n) WHERE {' AND '.join(clauses)} RETURN n ORDER BY n.event_time DESC LIMIT $limit", params=params)
        event_nodes = [self._node(row[0]) for row in result.result_set if self._node(row[0]).get("id")]
        return static_nodes[:bounded] + event_nodes

    def temporal_snapshot(self, ontology_id: str, at: str | None = None, limit: int = 300) -> dict[str, Any]:
        nodes = self._event_nodes(ontology_id, at=at, limit=limit)
        ids = [n["id"] for n in nodes]
        edges: list[dict[str, Any]] = []
        if ids and self.available:
            graph = self._graph(ontology_id)
            clauses = ["a._instance_id IN $ids", "b._instance_id IN $ids"]
            params: dict[str, Any] = {"ids": ids}
            if at:
                clauses.append("(r.valid_from IS NULL OR r.valid_from <= $at)")
                params["at"] = at
            result = graph.query(f"MATCH (a)-[r]->(b) WHERE {' AND '.join(clauses)} RETURN a._instance_id, b._instance_id, type(r), r", params=params)
            for source, target, rel_type, rel in result.result_set:
                props = dict(getattr(rel, "properties", {}) or {})
                if at and props.get("valid_to") and props["valid_to"] < at:
                    continue
                edges.append({"id": f"{source}:{rel_type}:{target}:{props.get('valid_from', '')}", "source": source, "target": target, "type": rel_type, "properties": props, "valid_from": props.get("valid_from"), "valid_to": props.get("valid_to")})
        total_nodes = len(nodes)
        total_edges = len(edges)
        if self.available:
            graph = self._graph(ontology_id)
            node_clauses = ["n._instance_id IS NOT NULL"]
            node_params: dict[str, Any] = {}
            if at:
                node_clauses.append("(n.event_time IS NULL OR n.event_time <= $at)")
                node_params["at"] = at
            count_nodes = graph.query(f"MATCH (n) WHERE {' AND '.join(node_clauses)} RETURN count(n)", params=node_params)
            total_nodes = int(count_nodes.result_set[0][0]) if count_nodes.result_set else total_nodes
            edge_clauses = ["a._instance_id IS NOT NULL", "b._instance_id IS NOT NULL"]
            edge_params: dict[str, Any] = {}
            if at:
                # A snapshot contains a relationship only when both endpoint
                # events have occurred by ``at``.  Observation edges do not
                # carry valid_from/valid_to themselves, so checking interval
                # properties alone would incorrectly report the full graph
                # edge count even for the first timestamp.
                edge_clauses.extend([
                    "(a.event_time IS NULL OR a.event_time <= $at)",
                    "(b.event_time IS NULL OR b.event_time <= $at)",
                    "(r.valid_from IS NULL OR r.valid_from <= $at)",
                    "(r.valid_to IS NULL OR r.valid_to >= $at)",
                ])
                edge_params["at"] = at
            count_edges = graph.query(f"MATCH (a)-[r]->(b) WHERE {' AND '.join(edge_clauses)} RETURN count(r)", params=edge_params)
            total_edges = int(count_edges.result_set[0][0]) if count_edges.result_set else total_edges
        return {"available": self.available, "graph_backend": "falkordb", "ontology_id": ontology_id, "at": at, "nodes": nodes, "edges": edges, "total_nodes": len(nodes), "total_edges": len(edges), "total_available_nodes": total_nodes, "total_available_edges": total_edges, "sample_limit": max(1, min(int(limit), 1000))}

    def temporal_timeline(self, ontology_id: str, entity_id: str | None = None, limit: int = 200) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "graph_backend": "falkordb", "events": []}
        graph = self._graph(ontology_id)
        clauses = ["n.event_time IS NOT NULL"]
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 1000))}
        if entity_id:
            clauses.append("(n._instance_id = $entity_id OR n.stream_id = $entity_id)")
            params["entity_id"] = entity_id
        result = graph.query(f"MATCH (n) WHERE {' AND '.join(clauses)} RETURN n ORDER BY n.event_time LIMIT $limit", params=params)
        events = []
        for row in result.result_set:
            node = self._node(row[0]); props = node.get("properties") or {}
            events.append({"id": node["id"], "timestamp": node.get("event_time"), "label": props.get("point_name") or props.get("name") or node.get("entity_type"), "entity_type": node.get("entity_type"), "value": props.get("value"), "stream_id": props.get("stream_id")})
        return {"available": True, "graph_backend": "falkordb", "ontology_id": ontology_id, "events": events, "count": len(events), "sample_limit": params["limit"]}

    def temporal_diff(self, ontology_id: str, from_at: str, to_at: str, limit: int = 1000) -> dict[str, Any]:
        before = self.temporal_snapshot(ontology_id, at=from_at, limit=limit)
        after = self.temporal_snapshot(ontology_id, at=to_at, limit=limit)
        bnodes = {n["id"]: n for n in before["nodes"]}; anodes = {n["id"]: n for n in after["nodes"]}
        bedges = {e["id"]: e for e in before["edges"]}; aedges = {e["id"]: e for e in after["edges"]}
        return {"available": self.available, "graph_backend": "falkordb", "ontology_id": ontology_id, "from": from_at, "to": to_at, "added_nodes": [anodes[k] for k in sorted(anodes.keys()-bnodes)][:limit], "removed_nodes": [bnodes[k] for k in sorted(bnodes.keys()-anodes)][:limit], "added_edges": [aedges[k] for k in sorted(aedges.keys()-bedges)][:limit], "removed_edges": [bedges[k] for k in sorted(bedges.keys()-aedges)][:limit], "before_counts": {"nodes": len(bnodes), "edges": len(bedges)}, "after_counts": {"nodes": len(anodes), "edges": len(aedges)}}

    def temporal_growth(self, ontology_id: str, limit: int = 1000) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "graph_backend": "falkordb", "points": []}
        graph = self._graph(ontology_id)
        result = graph.query("MATCH (n) WHERE n.event_time IS NOT NULL RETURN n.event_time, count(n) ORDER BY n.event_time LIMIT $limit", params={"limit": max(1, min(int(limit), 1000))})
        points = [{"timestamp": str(row[0]), "observations": int(row[1])} for row in result.result_set]
        running = 0
        for point in points:
            running += point["observations"]
            point["cumulative_nodes"] = running
        return {"available": True, "graph_backend": "falkordb", "ontology_id": ontology_id, "points": points}

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
