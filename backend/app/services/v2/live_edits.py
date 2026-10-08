"""Apply compiled live edits to the canonical instance projection.

The graph is the query surface and EntityInstance is its SQL companion.  A
failed SQL commit is compensated in the graph so Action runs do not report a
successful write to a different object store.
"""
from __future__ import annotations

from app.models.entity import Entity
from app.models.entity_instance import EntityInstance
from app.services.v2.object_query.core import FalkorReadAdapter
from app.services.v2.scenarios import ScenarioError


def apply_live_edits(db, ontology_id, edits, graph_service):
    if not graph_service.available:
        raise ScenarioError("graph_unavailable", "FalkorDB is unavailable", 503, "execution")
    graph = graph_service._graph(ontology_id)
    snapshot = FalkorReadAdapter(graph).read(ontology_id)
    objects = {key: dict(props) for key, props in snapshot.objects.items()}
    edges = set(snapshot.edges)
    before, after, results, undo = {}, {}, [], []

    def set_property(kind, object_id, name, value):
        graph.query(
            "MATCH (n:Instance {_instance_id: $id, _type: $kind, _ontology_id: $ontology}) SET n += $patch",
            params={"id": object_id, "kind": kind, "ontology": ontology_id, "patch": {name: value}},
        )

    def delete_object(kind, object_id):
        graph.query(
            "MATCH (n:Instance {_instance_id: $id, _type: $kind, _ontology_id: $ontology}) DETACH DELETE n",
            params={"id": object_id, "kind": kind, "ontology": ontology_id},
        )

    def add_link(link):
        graph_service.upsert_relations(ontology_id, [{"source": link.source.object_id,
            "target": link.target.object_id, "type": link.relation_type}])

    def remove_link(link):
        relation = graph_service._safe_relation_type(link.relation_type)
        graph.query(
            "MATCH (a:Instance {_instance_id: $source, _ontology_id: $ontology})"
            f"-[r:{relation}]->(b:Instance {{_instance_id: $target, _ontology_id: $ontology}}) DELETE r",
            params={"source": link.source.object_id, "target": link.target.object_id, "ontology": ontology_id},
        )

    def restore():
        for item in reversed(undo):
            try:
                op, args = item
                if op == "set":
                    set_property(*args)
                elif op == "delete":
                    delete_object(*args)
                elif op == "restore":
                    kind, object_id, props, edges = args
                    graph_service.upsert_instances(ontology_id, [{"id": object_id, "entity_type": kind, "properties": props}])
                    for source, relation, target in edges:
                        graph_service.upsert_relations(ontology_id, [{"source": source[1], "target": target[1], "type": relation}])
                elif op == "add_link":
                    add_link(args)
                elif op == "remove_link":
                    remove_link(args)
            except Exception:
                # Original failure is retained; an operator can replay the
                # recorded Action run if graph compensation also fails.
                pass

    try:
        for edit in edits:
            op = edit.op
            if op == "invoke_action":
                continue
            target = edit.target
            key = (target.concrete_type, target.object_id) if target else None
            if op in {"set_property", "unset_property", "delete_object"}:
                if key not in objects:
                    raise ScenarioError("object_not_found", "Action target is not in the live object projection", 404, "edits")
                row = db.query(EntityInstance).filter_by(ontology_id=ontology_id, id=target.object_id).with_for_update().first()
                if not row:
                    raise ScenarioError("projection_conflict", "Live object has no SQL instance", 409, "edits")
                props = dict(row.row_data or {})
                before.setdefault(target.object_id, dict(props))
                if op in {"set_property", "unset_property"}:
                    old = props.get(edit.property)
                    if "expected_old_value" in edit.model_fields_set and old != edit.expected_old_value:
                        raise ScenarioError("edit_conflict", "Target property changed since Action preview", 409, "edits")
                    if objects[key].get(edit.property) != old:
                        raise ScenarioError("projection_conflict", "Graph and SQL disagree on target property", 409, "edits")
                    undo.append(("set", (target.concrete_type, target.object_id, edit.property, objects[key].get(edit.property))))
                    if op == "set_property":
                        props[edit.property] = edit.value
                    else:
                        props.pop(edit.property, None)
                    row.row_data = props
                    set_property(target.concrete_type, target.object_id, edit.property, edit.value if op == "set_property" else None)
                    objects[key][edit.property] = edit.value if op == "set_property" else None
                    after[target.object_id] = props
                else:
                    incident = [edge for edge in edges if key in (edge[0], edge[2])]
                    undo.append(("restore", (target.concrete_type, target.object_id, objects[key], incident)))
                    db.delete(row)
                    delete_object(target.concrete_type, target.object_id)
                    objects.pop(key)
                    edges.difference_update(incident)
                results.append({"op": op, "target_id": target.object_id})
            elif op == "create_object":
                if key in objects or db.get(EntityInstance, target.object_id):
                    raise ScenarioError("edit_conflict", "Object ID already exists", 409, "edits")
                entity = db.query(Entity).filter(Entity.ontology_id == ontology_id,
                    (Entity.name_en == edit.object_type) | (Entity.name_cn == edit.object_type)).first()
                if not entity:
                    raise ScenarioError("object_type_not_found", "Published object type is unavailable", 409, "edits")
                props = dict(edit.properties or {})
                db.add(EntityInstance(id=target.object_id, entity_id=entity.id, ontology_id=ontology_id,
                                      row_identity=target.object_id, row_data=props))
                undo.append(("delete", (edit.object_type, target.object_id)))
                graph_service.upsert_instances(ontology_id, [{"id": target.object_id, "entity_type": edit.object_type, "properties": props}])
                objects[key] = props
                after[target.object_id] = props
                results.append({"op": op, "target_id": target.object_id})
            elif op in {"add_link", "remove_link"}:
                link = edit.link
                source = (link.source.concrete_type, link.source.object_id)
                target_key = (link.target.concrete_type, link.target.object_id)
                if source not in objects or target_key not in objects:
                    raise ScenarioError("object_not_found", "Link endpoint is not in the live projection", 404, "edits")
                edge = (source, graph_service._safe_relation_type(link.relation_type), target_key)
                exists = edge in edges
                if op == "add_link" and not exists:
                    undo.append(("remove_link", link)); add_link(link); edges.add(edge)
                elif op == "remove_link" and exists:
                    undo.append(("add_link", link)); remove_link(link); edges.discard(edge)
                results.append({"op": op, "relation_type": link.relation_type})
            else:
                raise ScenarioError("unsupported_edit", f"Unsupported compiled edit: {op}", 422, "edits")
        return before, after, results, restore
    except Exception:
        restore()
        raise
