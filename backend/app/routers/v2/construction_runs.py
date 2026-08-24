"""Construction Run and provenance APIs shared by all build modes."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.construction import ConstructionRun, EvidenceRef
from app.services.v2.construction_service import (
    add_evidence,
    create_run,
    serialize_evidence,
    serialize_run,
    update_run,
)

router = APIRouter(dependencies=[Depends(get_current_user)])
assertions_router = APIRouter(dependencies=[Depends(get_current_user)])
construction_root_router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class ConstructionRunCreate(BaseModel):
    mode: str = Field(pattern="^(temporal|multimodal|quality_benchmark)$")
    dataset_id: str | None = None
    model_name: str | None = None
    config: dict = {}


class RunUpdate(BaseModel):
    status: str | None = Field(default=None, pattern="^(queued|running|completed|failed|cancelled)$")
    progress: dict | None = None
    metrics: dict | None = None
    artifact_uri: str | None = None
    error: str | None = None


class EvidenceCreate(BaseModel):
    assertion_id: str
    assertion_kind: str = "node"
    extractor: str = Field(default="rule", pattern="^(rule|llm|ocr|bridge_fallback)$")
    source_file: str | None = None
    source_row: int | None = None
    source_media_id: str | None = None
    source_dataset_version: str | None = None
    model_name: str | None = None
    confidence: float | None = Field(default=None, ge=0, le=1)
    confidence_method: str = "not_calibrated"
    evidence_text: str | None = None
    content: object | None = None


@router.post("/{ontology_id}/construction-runs", status_code=202)
def create_construction_run(ontology_id: str, body: ConstructionRunCreate, db: Session = Depends(get_db)):
    run = create_run(
        db,
        ontology_id=ontology_id,
        mode=body.mode,
        dataset_id=body.dataset_id,
        model_name=body.model_name,
        config=body.config,
    )
    return serialize_run(run)


@router.get("/construction-runs/{run_id}")
def get_construction_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Construction run not found")
    return serialize_run(run)


@router.patch("/construction-runs/{run_id}")
def patch_construction_run(run_id: str, body: RunUpdate, db: Session = Depends(get_db)):
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Construction run not found")
    return serialize_run(update_run(db, run, **body.model_dump(exclude_none=True)))


@router.get("/construction-runs/{run_id}/evidence")
def list_run_evidence(run_id: str, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Construction run not found")
    refs = db.query(EvidenceRef).filter(EvidenceRef.construction_run_id == run_id).order_by(EvidenceRef.created_at.desc()).limit(limit).all()
    return {"run_id": run_id, "evidence": [serialize_evidence(ref) for ref in refs], "count": len(refs)}


@router.post("/construction-runs/{run_id}/evidence")
def create_run_evidence(run_id: str, body: EvidenceCreate, db: Session = Depends(get_db)):
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id).first()
    if not run:
        raise HTTPException(404, "Construction run not found")
    ref = add_evidence(db, run=run, **body.model_dump())
    return serialize_evidence(ref)


@router.get("/assertions/{assertion_id}/provenance")
def assertion_provenance(assertion_id: str, ontology_id: str | None = None, db: Session = Depends(get_db)):
    query = db.query(EvidenceRef).filter(EvidenceRef.assertion_id == assertion_id)
    if ontology_id:
        query = query.filter(EvidenceRef.ontology_id == ontology_id)
    refs = query.order_by(EvidenceRef.created_at.desc()).limit(1000).all()
    return {"assertion_id": assertion_id, "evidence": [serialize_evidence(ref) for ref in refs], "count": len(refs)}


@assertions_router.get("/assertions/{assertion_id}/provenance")
def assertion_provenance_root(assertion_id: str, ontology_id: str | None = None, db: Session = Depends(get_db)):
    return assertion_provenance(assertion_id, ontology_id=ontology_id, db=db)


@construction_root_router.get("/construction-runs/{run_id}")
def get_construction_run_root(run_id: str, db: Session = Depends(get_db)):
    return get_construction_run(run_id, db=db)


@construction_root_router.patch("/construction-runs/{run_id}")
def patch_construction_run_root(run_id: str, body: RunUpdate, db: Session = Depends(get_db)):
    return patch_construction_run(run_id, body=body, db=db)


@construction_root_router.get("/construction-runs/{run_id}/evidence")
def list_run_evidence_root(run_id: str, limit: int = Query(200, ge=1, le=1000), db: Session = Depends(get_db)):
    return list_run_evidence(run_id, limit=limit, db=db)


@construction_root_router.post("/construction-runs/{run_id}/evidence")
def create_run_evidence_root(run_id: str, body: EvidenceCreate, db: Session = Depends(get_db)):
    return create_run_evidence(run_id, body=body, db=db)
