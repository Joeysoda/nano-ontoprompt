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
            # A Redis PING verifies the service without creating a synthetic
            # ``nano_healthcheck`` graph that would pollute the demo graph
            # inventory every time /health or a graph endpoint is called.
            self._db.connection.ping()
            self._available = True
        except Exception:
            self._db = None

    @property
    def available(self) -> bool:
        return self._available

    def delete_graph(self, ontology_id: str) -> bool:
        """Delete the isolated graph for an ontology.

        FalkorDB exposes graph deletion on the client in recent releases.  A
        small command fallback keeps cleanup compatible with older clients
        without ever touching another ontology's graph.
        """
        if not self.available or not self._db:
            return False
        name = graph_name_for_ontology(ontology_id)
        try:
            deleter = getattr(self._db, "delete_graph", None)
            if callable(deleter):
                deleter(name)
            else:
                connection = getattr(self._db, "connection", None)
                if connection is None:
                    raise RuntimeError("FalkorDB client has no graph deletion API")
                connection.execute_command("GRAPH.DELETE", name)
                # Some FalkorDB releases leave the empty graph and telemetry
                # keys visible to GRAPH.LIST after GRAPH.DELETE. Remove only
                # the exact per-graph keys so the demo inventory is clean.
                connection.delete(name, f"telemetry{{{name}}}")
            return True
        except Exception:
            return False

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
        event_time: str | None = None,
        at: str | None = None,
        date_from: str | None = None,
        date_to: str | None = None,
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
        if event_time:
            clauses.append("r.event_time = $event_time")
            params["event_time"] = event_time
        if date_from:
            clauses.append("r.event_time >= $date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("r.event_time <= $date_to")
            params["date_to"] = date_to
        if at:
            clauses.append("(r.event_time IS NULL OR r.event_time <= $at)")
            clauses.append("(r.valid_from IS NULL OR r.valid_from <= $at)")
            clauses.append("(r.valid_to IS NULL OR r.valid_to >= $at)")
            params["at"] = at
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
        offset: int = 0,
        entity_type: str | None = None,
        seq_from: int | None = None,
        seq_to: int | None = None,
        relation_state: str = "all",
    ) -> dict[str, Any]:
        if not self.available:
            return {"nodes": [], "edges": [], "total_instances": 0, "graph_backend": "falkordb", "available": False}
        limit = max(1, min(int(limit), 500))
        offset = max(0, int(offset))
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
            f"MATCH (n) WHERE {' AND '.join(clauses)} RETURN n ORDER BY n.event_seq, n._instance_id SKIP $offset LIMIT $limit",
            params={**params, "offset": offset},
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
        total_clauses = ["n._instance_id IS NOT NULL"]
        total_params: dict[str, Any] = {}
        if entity_type:
            total_clauses.append("n._type = $entity_type"); total_params["entity_type"] = entity_type
        if seq_from is not None:
            total_clauses.append("n.event_seq >= $seq_from"); total_params["seq_from"] = int(seq_from)
        if seq_to is not None:
            total_clauses.append("n.event_seq <= $seq_to"); total_params["seq_to"] = int(seq_to)
        total = graph.query(f"MATCH (n) WHERE {' AND '.join(total_clauses)} RETURN count(n)", params=total_params)
        total_instances = int(total.result_set[0][0]) if total.result_set else 0
        return {
            "nodes": nodes,
            "edges": edges,
            "total_instances": total_instances,
            "returned": len(nodes),
            "offset": offset,
            "next_offset": offset + len(nodes) if offset + len(nodes) < total_instances else None,
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

    def _event_nodes(
        self,
        ontology_id: str,
        at: str | None = None,
        mode: str = "cumulative",
        date_from: str | None = None,
        date_to: str | None = None,
        country: str | None = None,
        event_type: str | None = None,
        category: str | None = None,
        intensity_min: float | None = None,
        intensity_max: float | None = None,
        participant: str | None = None,
        limit: int = 1000,
    ) -> list[dict[str, Any]]:
        """Read a bounded ICEWS/temporal snapshot from one ontology graph."""
        if not self.available:
            return []
        graph = self._graph(ontology_id)
        bounded = max(1, min(int(limit), 1000))
        mode = mode if mode in {"window", "cumulative"} else "cumulative"
        # Reserve most of the canvas for dated events, then add their direct
        # Actor/Category/Country/Location context.  Sampling static nodes first
        # would make a dense ICEWS graph appear to contain no relationships.
        event_limit = max(1, int(bounded * 0.65))
        clauses = ["n._instance_id IS NOT NULL", "n.event_time IS NOT NULL"]
        params: dict[str, Any] = {"limit": event_limit}
        if mode == "cumulative" and at:
            clauses.append("n.event_time <= $at")
            params["at"] = at
        if date_from:
            clauses.append("n.event_time >= $date_from")
            params["date_from"] = date_from
        if date_to:
            clauses.append("n.event_time <= $date_to")
            params["date_to"] = date_to
        if mode == "window" and at and not date_from and not date_to:
            clauses.append("n.event_time = $at")
            params["at"] = at
        if country:
            clauses.append("(toLower(coalesce(n.source_country, '')) CONTAINS $country OR toLower(coalesce(n.target_country, '')) CONTAINS $country OR toLower(coalesce(n.country, '')) CONTAINS $country)")
            params["country"] = str(country).casefold()
        if event_type:
            clauses.append("toLower(coalesce(n.event_type, '')) CONTAINS $event_type")
            params["event_type"] = str(event_type).casefold()
        if category:
            clauses.append("toLower(coalesce(n.cameo_code, '')) CONTAINS $category")
            params["category"] = str(category).casefold()
        if intensity_min is not None:
            clauses.append("n.intensity >= $intensity_min")
            params["intensity_min"] = float(intensity_min)
        if intensity_max is not None:
            clauses.append("n.intensity <= $intensity_max")
            params["intensity_max"] = float(intensity_max)
        if participant:
            clauses.append("(toLower(coalesce(n.source_name, '')) CONTAINS $participant OR toLower(coalesce(n.target_name, '')) CONTAINS $participant)")
            params["participant"] = str(participant).casefold()
        order = "n.event_time DESC, n._instance_id" if mode == "cumulative" else "n.event_time, n._instance_id"
        result = graph.query(f"MATCH (n) WHERE {' AND '.join(clauses)} RETURN n ORDER BY {order} LIMIT $limit", params=params)
        event_nodes = [self._node(row[0]) for row in result.result_set if self._node(row[0]).get("id")]
        context_nodes: list[dict[str, Any]] = []
        event_ids = [node["id"] for node in event_nodes]
        if event_ids and len(event_nodes) < bounded:
            context_result = graph.query(
                "MATCH (event)-[r]-(context) WHERE event._instance_id IN $event_ids "
                "AND context._instance_id IS NOT NULL AND context.event_time IS NULL "
                "RETURN context LIMIT $limit",
                params={"event_ids": event_ids, "limit": bounded - len(event_nodes)},
            )
            seen: set[str] = set()
            for row in context_result.result_set:
                node = self._node(row[0])
                if node.get("id") and node["id"] not in seen:
                    seen.add(node["id"]); context_nodes.append(node)
        if len(event_nodes) + len(context_nodes) < bounded:
            static_result = graph.query("MATCH (n) WHERE n._instance_id IS NOT NULL AND n.event_time IS NULL RETURN n LIMIT $limit", params={"limit": bounded})
            existing = {node["id"] for node in event_nodes + context_nodes}
            for row in static_result.result_set:
                node = self._node(row[0])
                if node.get("id") and node["id"] not in existing:
                    existing.add(node["id"]); context_nodes.append(node)
                if len(event_nodes) + len(context_nodes) >= bounded: break
        return event_nodes + context_nodes

    def temporal_snapshot(
        self,
        ontology_id: str,
        at: str | None = None,
        mode: str = "cumulative",
        date_from: str | None = None,
        date_to: str | None = None,
        country: str | None = None,
        event_type: str | None = None,
        category: str | None = None,
        intensity_min: float | None = None,
        intensity_max: float | None = None,
        participant: str | None = None,
        limit: int = 300,
    ) -> dict[str, Any]:
        # FactoryNet and uploaded ordinal datasets use numeric sequence values
        # rather than calendar dates. Keep this path separate from the legacy
        # ICEWS date predicates so a float ``time_s`` is never coerced to a
        # fabricated timestamp.
        if self.available and self._has_ordinal_nodes(ontology_id):
            return self._ordinal_snapshot(ontology_id, at=at, mode=mode, limit=limit)
        nodes = self._event_nodes(
            ontology_id, at=at, mode=mode, date_from=date_from, date_to=date_to,
            country=country, event_type=event_type, category=category,
            intensity_min=intensity_min,
            intensity_max=intensity_max, participant=participant, limit=limit,
        )
        ids = [n["id"] for n in nodes]
        edges: list[dict[str, Any]] = []
        if ids and self.available:
            graph = self._graph(ontology_id)
            clauses = ["a._instance_id IN $ids", "b._instance_id IN $ids"]
            params: dict[str, Any] = {"ids": ids}
            if mode == "cumulative" and at:
                clauses.append("(r.valid_from IS NULL OR r.valid_from <= $at)")
                params["at"] = at
            if date_from:
                clauses.append("(r.event_time IS NULL OR r.event_time >= $date_from)")
                params["date_from"] = date_from
            if date_to:
                clauses.append("(r.event_time IS NULL OR r.event_time <= $date_to)")
                params["date_to"] = date_to
            if country:
                clauses.append("(toLower(coalesce(a.source_country, '')) CONTAINS $country OR toLower(coalesce(a.target_country, '')) CONTAINS $country OR toLower(coalesce(a.country, '')) CONTAINS $country OR toLower(coalesce(b.source_country, '')) CONTAINS $country OR toLower(coalesce(b.target_country, '')) CONTAINS $country OR toLower(coalesce(b.country, '')) CONTAINS $country)")
                params["country"] = str(country).casefold()
            if event_type:
                clauses.append("(toLower(coalesce(a.event_type, '')) CONTAINS $event_type OR toLower(coalesce(b.event_type, '')) CONTAINS $event_type)")
                params["event_type"] = str(event_type).casefold()
            if category:
                clauses.append("(toLower(coalesce(a.cameo_code, '')) CONTAINS $category OR toLower(coalesce(b.cameo_code, '')) CONTAINS $category)")
                params["category"] = str(category).casefold()
            if intensity_min is not None:
                clauses.append("(a.intensity >= $intensity_min OR b.intensity >= $intensity_min)")
                params["intensity_min"] = float(intensity_min)
            if intensity_max is not None:
                clauses.append("(a.intensity <= $intensity_max OR b.intensity <= $intensity_max)")
                params["intensity_max"] = float(intensity_max)
            if participant:
                clauses.append("(toLower(coalesce(a.source_name, '')) CONTAINS $participant OR toLower(coalesce(a.target_name, '')) CONTAINS $participant OR toLower(coalesce(b.source_name, '')) CONTAINS $participant OR toLower(coalesce(b.target_name, '')) CONTAINS $participant)")
                params["participant"] = str(participant).casefold()
            result = graph.query(f"MATCH (a)-[r]->(b) WHERE {' AND '.join(clauses)} RETURN a._instance_id, b._instance_id, type(r), r", params=params)
            for source, target, rel_type, rel in result.result_set:
                props = dict(getattr(rel, "properties", {}) or {})
                if mode == "cumulative" and at and props.get("valid_to") and props["valid_to"] < at:
                    continue
                edges.append({"id": f"{source}:{rel_type}:{target}:{props.get('valid_from', '')}", "source": source, "target": target, "type": rel_type, "properties": props, "valid_from": props.get("valid_from"), "valid_to": props.get("valid_to")})
        total_nodes = len(nodes)
        total_edges = len(edges)
        if self.available:
            graph = self._graph(ontology_id)
            node_clauses = ["n._instance_id IS NOT NULL"]
            node_params: dict[str, Any] = {}
            if mode == "cumulative" and at:
                node_clauses.append("(n.event_time IS NULL OR n.event_time <= $at)")
                node_params["at"] = at
            if date_from:
                node_clauses.append("(n.event_time IS NULL OR n.event_time >= $date_from)")
                node_params["date_from"] = date_from
            if date_to:
                node_clauses.append("(n.event_time IS NULL OR n.event_time <= $date_to)")
                node_params["date_to"] = date_to
            if mode == "window" and at and not date_from and not date_to:
                node_clauses.append("(n.event_time IS NULL OR n.event_time = $at)")
                node_params["at"] = at
            if country:
                node_clauses.append("(n.event_time IS NULL OR toLower(coalesce(n.source_country, '')) CONTAINS $country OR toLower(coalesce(n.target_country, '')) CONTAINS $country OR toLower(coalesce(n.country, '')) CONTAINS $country)")
                node_params["country"] = str(country).casefold()
            if event_type:
                node_clauses.append("(n.event_time IS NULL OR toLower(coalesce(n.event_type, '')) CONTAINS $event_type)")
                node_params["event_type"] = str(event_type).casefold()
            if category:
                node_clauses.append("(n.event_time IS NULL OR toLower(coalesce(n.cameo_code, '')) CONTAINS $category)")
                node_params["category"] = str(category).casefold()
            if intensity_min is not None:
                node_clauses.append("(n.event_time IS NULL OR n.intensity >= $intensity_min)")
                node_params["intensity_min"] = float(intensity_min)
            if intensity_max is not None:
                node_clauses.append("(n.event_time IS NULL OR n.intensity <= $intensity_max)")
                node_params["intensity_max"] = float(intensity_max)
            if participant:
                node_clauses.append("(n.event_time IS NULL OR toLower(coalesce(n.source_name, '')) CONTAINS $participant OR toLower(coalesce(n.target_name, '')) CONTAINS $participant)")
                node_params["participant"] = str(participant).casefold()
            count_nodes = graph.query(f"MATCH (n) WHERE {' AND '.join(node_clauses)} RETURN count(n)", params=node_params)
            total_nodes = int(count_nodes.result_set[0][0]) if count_nodes.result_set else total_nodes
            edge_clauses = ["a._instance_id IS NOT NULL", "b._instance_id IS NOT NULL"]
            edge_params: dict[str, Any] = {}
            if mode == "cumulative" and at:
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
            if date_from:
                edge_clauses.append("(r.event_time IS NULL OR r.event_time >= $date_from)")
                edge_params["date_from"] = date_from
            if date_to:
                edge_clauses.append("(r.event_time IS NULL OR r.event_time <= $date_to)")
                edge_params["date_to"] = date_to
            if mode == "window" and at and not date_from and not date_to:
                edge_clauses.append("(r.event_time IS NULL OR r.event_time = $at)")
                edge_params["at"] = at
            if country:
                edge_clauses.append("(toLower(coalesce(a.source_country, '')) CONTAINS $country OR toLower(coalesce(a.target_country, '')) CONTAINS $country OR toLower(coalesce(a.country, '')) CONTAINS $country OR toLower(coalesce(b.source_country, '')) CONTAINS $country OR toLower(coalesce(b.target_country, '')) CONTAINS $country OR toLower(coalesce(b.country, '')) CONTAINS $country)")
                edge_params["country"] = str(country).casefold()
            if event_type:
                edge_clauses.append("(toLower(coalesce(a.event_type, '')) CONTAINS $event_type OR toLower(coalesce(b.event_type, '')) CONTAINS $event_type)")
                edge_params["event_type"] = str(event_type).casefold()
            if category:
                edge_clauses.append("(toLower(coalesce(a.cameo_code, '')) CONTAINS $category OR toLower(coalesce(b.cameo_code, '')) CONTAINS $category)")
                edge_params["category"] = str(category).casefold()
            if intensity_min is not None:
                edge_clauses.append("(a.intensity >= $intensity_min OR b.intensity >= $intensity_min)")
                edge_params["intensity_min"] = float(intensity_min)
            if intensity_max is not None:
                edge_clauses.append("(a.intensity <= $intensity_max OR b.intensity <= $intensity_max)")
                edge_params["intensity_max"] = float(intensity_max)
            if participant:
                edge_clauses.append("(toLower(coalesce(a.source_name, '')) CONTAINS $participant OR toLower(coalesce(a.target_name, '')) CONTAINS $participant OR toLower(coalesce(b.source_name, '')) CONTAINS $participant OR toLower(coalesce(b.target_name, '')) CONTAINS $participant)")
                edge_params["participant"] = str(participant).casefold()
            count_edges = graph.query(f"MATCH (a)-[r]->(b) WHERE {' AND '.join(edge_clauses)} RETURN count(r)", params=edge_params)
            total_edges = int(count_edges.result_set[0][0]) if count_edges.result_set else total_edges
        return {"available": self.available, "graph_backend": "falkordb", "ontology_id": ontology_id, "at": at, "mode": mode, "date_from": date_from, "date_to": date_to, "nodes": nodes, "edges": edges, "total_nodes": len(nodes), "total_edges": len(edges), "total_available_nodes": total_nodes, "total_available_edges": total_edges, "sample_limit": max(1, min(int(limit), 1000))}

    def _has_ordinal_nodes(self, ontology_id: str) -> bool:
        if not self.available:
            return False
        result = self._graph(ontology_id).query("MATCH (n) WHERE n.event_seq IS NOT NULL RETURN count(n)")
        return bool(result.result_set and int(result.result_set[0][0]) > 0)

    def _ordinal_snapshot(self, ontology_id: str, at: str | None, mode: str, limit: int) -> dict[str, Any]:
        graph_data = self.get_graph_data(ontology_id, limit=min(max(int(limit), 1), 500))
        all_nodes = graph_data.get("nodes", [])
        try:
            point = float(at) if at not in (None, "") else None
        except (TypeError, ValueError):
            point = None
        if point is None:
            selected = all_nodes
        elif mode == "window":
            selected = [n for n in all_nodes if n.get("event_seq") is None or float(n.get("event_seq")) == point]
        else:
            selected = [n for n in all_nodes if n.get("event_seq") is None or float(n.get("event_seq")) <= point]
        ids = {n.get("id") for n in selected}
        edges = [e for e in graph_data.get("edges", []) if e.get("source") in ids and e.get("target") in ids]
        return {"available": True, "graph_backend": "falkordb", "ontology_id": ontology_id, "at": at, "mode": mode,
                "time_kind": "ordinal", "nodes": selected[:limit], "edges": edges[:limit * 3],
                "total_nodes": len(selected), "total_edges": len(edges),
                "total_available_nodes": graph_data.get("total_instances", len(all_nodes)),
                "total_available_edges": len(graph_data.get("edges", [])), "sample_limit": limit}

    def temporal_timeline(self, ontology_id: str, entity_id: str | None = None, category: str | None = None, limit: int = 200) -> dict[str, Any]:
        if not self.available:
            return {"available": False, "graph_backend": "falkordb", "events": []}
        graph = self._graph(ontology_id)
        if self._has_ordinal_nodes(ontology_id):
            buckets = graph.query("MATCH (n) WHERE n.event_seq IS NOT NULL RETURN n.event_seq, count(n) ORDER BY n.event_seq")
            points = [{"timestamp": str(row[0]), "count": int(row[1])} for row in buckets.result_set]
            result = graph.query("MATCH (n) WHERE n.event_seq IS NOT NULL RETURN n ORDER BY n.event_seq LIMIT $limit", params={"limit": max(1, min(int(limit), 1000))})
            events = []
            for row in result.result_set:
                node = self._node(row[0]); props = node.get("properties") or {}
                events.append({"id": node["id"], "timestamp": node.get("event_seq"), "label": props.get("episode_id") or props.get("phase") or node.get("entity_type"), "entity_type": node.get("entity_type"), "value": props.get("time_s") or props.get("elapsed_seconds")})
            return {"available": True, "graph_backend": "falkordb", "ontology_id": ontology_id, "events": events, "count": len(events), "total_events": sum(p["count"] for p in points), "dates": [p["timestamp"] for p in points], "buckets": points, "time_kind": "ordinal", "sample_limit": limit}
        clauses = ["n.event_time IS NOT NULL"]
        params: dict[str, Any] = {"limit": max(1, min(int(limit), 1000))}
        if entity_id:
            clauses.append("(n._instance_id = $entity_id OR n.stream_id = $entity_id)")
            params["entity_id"] = entity_id
        if category:
            clauses.append("(toLower(coalesce(n.cameo_code, '')) CONTAINS $category OR toLower(coalesce(n.event_type, '')) CONTAINS $category)")
            params["category"] = str(category).casefold()
        # Keep the event table bounded for the browser, but compute the date
        # buckets without that limit.  ICEWS has hundreds of events per day,
        # so deriving the slider dates from the first 500 events would hide
        # later days entirely.  The buckets are also the API's deterministic
        # day-level aggregation used by the Semantica-style timeline.
        bucket_result = graph.query(
            f"MATCH (n) WHERE {' AND '.join(clauses)} "
            "RETURN n.event_time, count(n) ORDER BY n.event_time",
            params={key: value for key, value in params.items() if key != "limit"},
        )
        buckets = [
            {"timestamp": str(row[0]), "count": int(row[1])}
            for row in bucket_result.result_set
            if row and row[0] is not None
        ]
        result = graph.query(f"MATCH (n) WHERE {' AND '.join(clauses)} RETURN n ORDER BY n.event_time LIMIT $limit", params=params)
        events = []
        for row in result.result_set:
            node = self._node(row[0]); props = node.get("properties") or {}
            events.append({"id": node["id"], "timestamp": node.get("event_time"), "label": props.get("event_type") or props.get("point_name") or props.get("name") or node.get("entity_type"), "entity_type": node.get("entity_type"), "value": props.get("value") if props.get("value") is not None else props.get("intensity"), "category": props.get("cameo_code"), "stream_id": props.get("stream_id")})
        return {
            "available": True,
            "graph_backend": "falkordb",
            "ontology_id": ontology_id,
            "events": events,
            "count": len(events),
            "total_events": sum(bucket["count"] for bucket in buckets),
            "dates": [bucket["timestamp"] for bucket in buckets],
            "buckets": buckets,
            "sample_limit": params["limit"],
        }

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
        if self._has_ordinal_nodes(ontology_id):
            result = graph.query("MATCH (n) WHERE n.event_seq IS NOT NULL RETURN n.event_seq, count(n) ORDER BY n.event_seq LIMIT $limit", params={"limit": max(1, min(int(limit), 1000))})
            points = [{"timestamp": str(row[0]), "observations": int(row[1])} for row in result.result_set]
            running = 0
            for point in points:
                running += point["observations"]; point["cumulative_nodes"] = running
            return {"available": True, "graph_backend": "falkordb", "ontology_id": ontology_id, "time_kind": "ordinal", "points": points}
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
