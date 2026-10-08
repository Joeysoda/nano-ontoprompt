"""Published Object View configuration consumed by the shared panel/full view."""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.deps import get_current_user, get_db
from app.models.ontology import OntologyProject
from app.models.v2.object_view import ObjectViewConfiguration
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import load_sql_metadata

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
    project = db.get(OntologyProject, ontology_id)
    if not project or (user.role != "admin" and project.created_by != user.id):
        raise HTTPException(404, "Ontology not found or inaccessible")
    metadata = load_sql_metadata(db, ontology_id)
    try:
        return metadata.resolve_type("object", object_type, "object_type")
    except ObjectQueryError as exc:
        raise HTTPException(404, {"code": exc.code, "message": exc.message}) from exc


@router.get("/{ontology_id}/object-views/{object_type}")
def get_object_view(ontology_id: str, object_type: str, db: Session = Depends(get_db), user=Depends(get_current_user)):
    type_meta = _metadata(db, ontology_id, object_type, user)
    from app.services.v2.object_query.core import metadata_digest
    digest = metadata_digest(load_sql_metadata(db, ontology_id))
    row = db.query(ObjectViewConfiguration).filter_by(ontology_id=ontology_id, object_type=object_type).order_by(ObjectViewConfiguration.version.desc()).first()
    compatible = bool(row and row.metadata_digest == digest)
    standard = {"tabs": [{"id": "properties", "title": "属性", "properties": list(type_meta.properties), "layout": "list", "show_in_panel": True}]}
    return {"object_type": object_type, "standard": standard, "configured": row.view_schema if compatible else None,
            "configured_version": row.version if compatible else None, "configuration_stale": bool(row and not compatible)}


@router.put("/{ontology_id}/object-views/{object_type}")
def publish_object_view(ontology_id: str, object_type: str, body: ViewConfig,
                        db: Session = Depends(get_db), user=Depends(get_current_user)):
    type_meta = _metadata(db, ontology_id, object_type, user)
    if len({tab.id for tab in body.tabs}) != len(body.tabs):
        raise HTTPException(422, {"code": "duplicate_tab", "message": "View tab IDs must be unique"})
    for tab in body.tabs:
        unknown = set(tab.properties) - set(type_meta.properties)
        if unknown or (tab.visible_when and tab.visible_when.property not in type_meta.properties):
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
