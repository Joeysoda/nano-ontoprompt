"""Blind, event-at-a-time FactoryNet demo entry points.

The existing temporal-stream endpoints remain the source of truth for control,
SSE, graph inspection and snapshot publishing.  This router only prepares a
safe default source/ontology pair so the group-meeting demo does not require a
hard-coded ontology UUID or a second setup form.
"""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.entity import Entity
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.construction import ConstructionRun
from app.services.v2.datasets.factorynet_installer import (
    FACTORYNET_SOURCE_ID,
    find_factorynet_dataset,
    install_factorynet_dataset,
)
from app.services.v2.temporal_stream_service import (
    StreamError,
    create_stream_run,
    serialize_stream,
)


router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class DemoSessionCreate(BaseModel):
    speed: float = Field(default=1.0, gt=0, le=20)
    episode_id: str | None = Field(default=None, min_length=1, max_length=200)
    start_ordinal: float | None = None
    end_ordinal: float | None = None


_FACTORYNET_ENTITIES = {
    "Machine",
    "Episode",
    "Observation",
    "ProcessPhase",
    "ToolCondition",
    "SensorChannel",
    "InspectionResult",
}


def _candidate_ontology(db: Session, dataset_id: str | None = None) -> OntologyProject | None:
    """Find a real FactoryNet ontology without relying on a UUID or name only."""
    projects = db.query(OntologyProject).filter(
        OntologyProject.data_class == "temporal",
    ).order_by(OntologyProject.updated_at.desc(), OntologyProject.created_at.desc()).all()
    for project in projects:
        names = {
            str(row.name_en or row.canonical_id or row.name_cn or "").strip()
            for row in db.query(Entity).filter(Entity.ontology_id == project.id).all()
        }
        if not _FACTORYNET_ENTITIES.issubset(names):
            continue
        if "factorynet" in project.name.lower() or "factorynet" in (project.description or "").lower():
            return project
        if dataset_id and db.query(ConstructionRun.id).filter(
            ConstructionRun.ontology_id == project.id,
            ConstructionRun.dataset_id == dataset_id,
        ).first():
            return project
    return None


def _readiness_payload(db: Session) -> dict[str, Any]:
    dataset = find_factorynet_dataset(db)
    version = None
    if dataset and dataset.latest_version_id:
        from app.models.v2.dataset import DatasetVersion
        version = db.query(DatasetVersion).filter(DatasetVersion.id == dataset.latest_version_id).first()
    project = _candidate_ontology(db, dataset.id if dataset else None)
    return {
        "source_id": FACTORYNET_SOURCE_ID,
        "source_ready": bool(dataset and version and version.storage_uri),
        "dataset_id": dataset.id if dataset else None,
        "dataset_name": dataset.name if dataset else "FactoryNet CNC",
        "dataset_version_id": version.id if version else None,
        "ontology_ready": project is not None,
        "ontology_id": project.id if project else None,
        "ontology_name": project.name if project else None,
        "mode": "simulated_live",
        "horizon_known": False,
    }


@router.get("/dynamic-data/demo-readiness")
def demo_readiness(db: Session = Depends(get_db)):
    return _readiness_payload(db)


@router.post("/dynamic-data/demo-sessions", status_code=status.HTTP_201_CREATED)
def create_demo_session(
    body: DemoSessionCreate,
    db: Session = Depends(get_db),
    user: User = Depends(require_editor),
):
    dataset = find_factorynet_dataset(db)
    if dataset is None:
        try:
            install_factorynet_dataset(db)
            dataset = find_factorynet_dataset(db)
        except Exception as exc:
            raise HTTPException(
                status_code=424,
                detail={"code": "SOURCE_NOT_READY", "message": f"FactoryNet 样例尚未准备好：{exc}"},
            ) from exc
    if dataset is None or not dataset.latest_version_id:
        raise HTTPException(status_code=424, detail={"code": "SOURCE_NOT_READY", "message": "FactoryNet 数据版本尚未准备好"})

    project = _candidate_ontology(db, dataset.id)
    if project is None:
        project = OntologyProject(
            name="FactoryNet CNC 动态演示",
            domain="制造",
            description="FactoryNet 逐事件动态数据构建演示",
            build_mode="temporal_stream",
            data_class="temporal",
            status="published",
            created_by=user.id,
        )
        db.add(project)
        db.flush()
        from app.services.v2.temporal_stream_service import _ensure_factorynet_schema, ensure_schema_revision
        _ensure_factorynet_schema(db, project.id)
        ensure_schema_revision(db, project)
        db.commit()
        db.refresh(project)

    try:
        run = create_stream_run(
            db,
            project.id,
            dataset_id=dataset.id,
            dataset_version_id=dataset.latest_version_id,
            episode_ids=[body.episode_id] if body.episode_id else [],
            start_ordinal=body.start_ordinal,
            end_ordinal=body.end_ordinal,
            speed=body.speed,
            source_mode="simulated_live",
            config={"presentation": "blind_stream"},
            created_by=user.id,
        )
    except StreamError as exc:
        raise HTTPException(status_code=422, detail={"code": exc.code, "message": str(exc), **exc.extra}) from exc
    return serialize_stream(run, db=db)


__all__ = ["router"]
