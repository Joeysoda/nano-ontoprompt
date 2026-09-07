"""Workbench dashboard metrics used by the first viewport."""
from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.action import Action
from app.models.audit_task import AuditTask
from app.models.entity import Entity
from app.models.logic import LogicRule
from app.models.ontology import OntologyProject
from app.models.v2.construction import ConstructionRun
from app.models.v2.dataset import Dataset

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


def _count(db: Session, model: Any, *criteria: Any) -> int:
    try:
        query = db.query(model)
        if criteria:
            query = query.filter(*criteria)
        return int(query.count())
    except Exception:
        return 0


@router.get("")
def dashboard(db: Session = Depends(get_db)) -> dict[str, Any]:
    recent: list[dict[str, Any]] = []
    try:
        projects = db.query(OntologyProject).order_by(OntologyProject.updated_at.desc()).limit(8).all()
        for project in projects:
            recent.append({
                "id": project.id,
                "name": project.name,
                "domain": project.domain,
                "status": project.status,
                "current_revision_id": getattr(project, "current_revision_id", None),
                "entity_count": _count(db, Entity, Entity.ontology_id == project.id),
                "logic_count": _count(db, LogicRule, LogicRule.ontology_id == project.id),
                "action_count": _count(db, Action, Action.ontology_id == project.id),
                "updated_at": project.updated_at.isoformat() if project.updated_at else None,
            })
    except Exception:
        recent = []

    classes: dict[str, int] = {"regular": 0, "temporal": 0, "multimodal": 0}
    try:
        for data_class, count in db.query(Dataset.data_class, func.count(Dataset.id)).group_by(Dataset.data_class).all():
            if data_class in classes:
                classes[data_class] = int(count)
    except Exception:
        pass

    running = 0
    review = 0
    try:
        running = int(db.query(ConstructionRun).filter(ConstructionRun.status.in_(["queued", "running", "waiting_for_model"])).count())
    except Exception:
        pass
    try:
        review = int(db.query(AuditTask).filter(AuditTask.status.in_(["queued", "running", "waiting_for_model", "completed"])).count())
    except Exception:
        pass

    return {
        "ontology_count": _count(db, OntologyProject),
        "entity_count": _count(db, Entity),
        "logic_count": _count(db, LogicRule),
        "action_count": _count(db, Action),
        "dataset_count": _count(db, Dataset),
        "task_count": running,
        "review_count": review,
        "data_class_counts": classes,
        "recent_ontologies": recent,
    }
