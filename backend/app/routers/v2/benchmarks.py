"""Gold-based benchmark endpoints used by OSKGC/CQ4OE-style experiments."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.construction import ConstructionRun
from app.services.v2.benchmark_service import evaluate
from app.services.v2.construction_service import create_run, serialize_run, update_run

router = APIRouter(prefix="/benchmarks", dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class BenchmarkRunRequest(BaseModel):
    benchmark: str = Field(min_length=1)
    ontology_id: str
    dataset_id: str | None = None
    model_name: str | None = None
    predicted_entities: list = []
    gold_entities: list = []
    predicted_triples: list = []
    gold_triples: list = []
    schema_nodes: list[dict] = []
    schema: dict = {}


@router.post("/runs", status_code=202)
def create_benchmark_run(body: BenchmarkRunRequest, db: Session = Depends(get_db)):
    run = create_run(db, ontology_id=body.ontology_id, dataset_id=body.dataset_id, mode="quality_benchmark", model_name=body.model_name, config={"benchmark": body.benchmark})
    metrics = evaluate(body.predicted_entities, body.gold_entities, body.predicted_triples, body.gold_triples, body.schema_nodes, body.schema or None)
    update_run(db, run, status="completed", progress={"completed": 1, "total": 1}, metrics=metrics)
    return serialize_run(run)


@router.get("/runs/{run_id}")
def get_benchmark_run(run_id: str, db: Session = Depends(get_db)):
    run = db.query(ConstructionRun).filter(ConstructionRun.id == run_id, ConstructionRun.mode == "quality_benchmark").first()
    if not run:
        raise HTTPException(404, "Benchmark run not found")
    return serialize_run(run)
