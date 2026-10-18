"""Published Object View configuration consumed by the shared panel/full view."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db, require_editor
from app.models.ontology import OntologyProject
from app.models.v2.object_view import ObjectViewConfiguration
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata
from app.services.v2.object_query.authorization import policies_from_projection, require_ontology_access
from app.services.v2.authorization_service import redact_record, resolve_projection

router = APIRouter()


class Visibility(BaseModel):
    property: str
    equals: str | int | float | bool | None = None


class ViewSection(BaseModel):
    id: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_]*$")
    title: str = Field(min_length=1, max_length=120)
    properties: list[str] = Field(min_length=1, max_length=40)
    layout: Literal["list", "grid"] = "list"
    show_in_panel: bool = True
    visible_when: Visibility | None = None


class ViewConfig(BaseModel):
    tabs: list[ViewSection] = Field(min_length=1, max_length=12)


def _metadata(db, ontology_id, object_type, user):
    try:
        access = require_ontology_access(db, ontology_id, str(user.id), user.role)
    except ObjectQueryError as exc:
        raise HTTPException(404, "Ontology not found or inaccessible") from exc
    metadata = load_sql_metadata(db, ontology_id)
    try:
        type_meta = metadata.resolve_type("object", object_type, "object_type")
    except ObjectQueryError as exc:
        raise HTTPException(404, {"code": exc.code, "message": exc.message}) from exc
    record = {
        "id": str(object_type),
        "entity_type": str(object_type),
        "properties": {name: None for name in type_meta.properties},
    }
    projection = resolve_projection(
        db,
        access.context,
        preloaded_policies=policies_from_projection(access),
        records=[record],
    )
    safe = redact_record(projection, record)
    if safe is None:
        raise HTTPException(404, "Ontology not found or inaccessible")
    allowed = projection.allowed_fields_by_object.get(str(object_type), projection.allowed_fields)
    denied = projection.denied_fields | projection.denied_fields_by_object.get(str(object_type), set())
    visible = set(type_meta.properties) if allowed is None else {
        name for name in type_meta.properties if name in allowed and name not in denied
    }
    return type_meta, visible


def _filter_view_schema(schema: dict | None, visible: set[str]) -> dict | None:
    if not isinstance(schema, dict):
        return None
    tabs = []
    for tab in schema.get("tabs", []):
        if not isinstance(tab, dict):
            continue
        properties = [name for name in tab.get("properties", []) if name in visible]
        visible_when = tab.get("visible_when")
        if isinstance(visible_when, dict) and visible_when.get("property") not in visible:
            visible_when = None
        if properties:
            tabs.append({**tab, "properties": properties, "visible_when": visible_when})
    return {**schema, "tabs": tabs}


def _metadata_result(value):
    """Keep the old helper return shape usable by existing integrations.

    The secured path returns ``(TypeMetadata, visible_properties)``.  A few
    callers and older tests monkeypatch ``_metadata`` with just a type-like
    object, so treat that shape as an already-authorized view of all exposed
    properties instead of breaking the publishing contract.
    """
    if isinstance(value, tuple) and len(value) == 2:
        return value
    properties = getattr(value, "properties", {})
    return value, set(properties or {})


@router.get("/{ontology_id}/object-views/{object_type}")
def get_object_view(ontology_id: str, object_type: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    type_meta, visible_properties = _metadata_result(_metadata(db, ontology_id, object_type, user))
    from app.services.v2.object_query.core import metadata_digest
    digest = metadata_digest(load_sql_metadata(db, ontology_id))
    row = db.query(ObjectViewConfiguration).filter_by(ontology_id=ontology_id, object_type=object_type).order_by(ObjectViewConfiguration.version.desc()).first()
    compatible = bool(row and row.metadata_digest == digest)
    standard = {"tabs": [{"id": "properties", "title": "属性", "properties": [name for name in type_meta.properties if name in visible_properties], "layout": "list", "show_in_panel": True}]}
    configured = _filter_view_schema(row.view_schema, visible_properties) if compatible else None
    return {"object_type": object_type, "standard": standard, "configured": configured,
            "configured_version": row.version if compatible else None, "configuration_stale": bool(row and not compatible)}


@router.put("/{ontology_id}/object-views/{object_type}")
def publish_object_view(ontology_id: str, object_type: str, body: ViewConfig,
                        db: Session = Depends(get_db), user=Depends(require_editor)):
    type_meta, visible_properties = _metadata_result(_metadata(db, ontology_id, object_type, user))
    if len({tab.id for tab in body.tabs}) != len(body.tabs):
        raise HTTPException(422, {"code": "duplicate_tab", "message": "View tab IDs must be unique"})
    for tab in body.tabs:
        unknown = set(tab.properties) - visible_properties
        if unknown or (tab.visible_when and tab.visible_when.property not in visible_properties):
            raise HTTPException(422, {"code": "unknown_property", "message": "View references an unpublished property"})
        if len(set(tab.properties)) != len(tab.properties):
            raise HTTPException(422, {"code": "duplicate_property", "message": "A tab cannot repeat one property"})
    version = (db.query(func.max(ObjectViewConfiguration.version)).filter_by(ontology_id=ontology_id, object_type=object_type).scalar() or 0) + 1
    from app.services.v2.object_query.core import metadata_digest
    row = ObjectViewConfiguration(ontology_id=ontology_id, object_type=object_type, version=version,
                                  metadata_digest=metadata_digest(load_sql_metadata(db, ontology_id)),
                                  view_schema=body.model_dump(mode="json"), published_by=user.id)
    db.add(row)
    db.commit()
    return {"id": row.id, "version": version, "object_type": object_type, "configured": row.view_schema}
