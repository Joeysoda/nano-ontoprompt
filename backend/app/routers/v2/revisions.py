"""Ontology revision history, comparison and non-destructive restore."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user, require_editor
from app.models.ontology import OntologyProject
from app.models.ontology_revision import OntologyRevision
from app.models.v2.dynamic_ontology import OntologyChange
from app.services.v2.revision_service import compare_revisions, create_revision, materialize_snapshot, serialize_revision, snapshot_ontology

router = APIRouter(dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


@router.get("/{ontology_id}/revisions")
def list_revisions(ontology_id: str, db: Session = Depends(get_db)):
    if not db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first():
        raise HTTPException(404, "Ontology not found")
    rows = db.query(OntologyRevision).filter(OntologyRevision.ontology_id == ontology_id).order_by(OntologyRevision.revision_no.desc()).all()
    return {"ontology_id": ontology_id, "current_revision_id": getattr(db.query(OntologyProject).filter(OntologyProject.id == ontology_id).first(), "current_revision_id", None), "revisions": [serialize_revision(row) for row in rows], "count": len(rows)}


@router.get("/{ontology_id}/revisions/compare")
def compare(ontology_id: str, left: str = Query(...), right: str = Query(...), db: Session = Depends(get_db)):
    rows = db.query(OntologyRevision).filter(OntologyRevision.ontology_id == ontology_id, OntologyRevision.id.in_([left, right])).all()
    by_id = {row.id: row for row in rows}
    if left not in by_id or right not in by_id:
        raise HTTPException(404, "版本不存在")
    return compare_revisions(by_id[left], by_id[right])


@router.post("/{ontology_id}/revisions/{revision_id}/restore")
def restore(ontology_id: str, revision_id: str, db: Session = Depends(get_db), _=Depends(require_editor)):
    target = db.query(OntologyRevision).filter(OntologyRevision.id == revision_id, OntologyRevision.ontology_id == ontology_id).first()
    if not target:
        raise HTTPException(404, "版本不存在")
    current = db.query(OntologyRevision).filter(
        OntologyRevision.ontology_id == ontology_id, OntologyRevision.is_current.is_(True)
    ).order_by(OntologyRevision.revision_no.desc()).first()
    before = snapshot_ontology(db, ontology_id)
    try:
        materialize_snapshot(db, ontology_id, target.snapshot_json or {})
        restored_snapshot = snapshot_ontology(db, ontology_id)
        revision = create_revision(
            db, ontology_id, snapshot=restored_snapshot, parent_revision_id=current.id if current else target.id,
            summary={**(target.summary or {}), "restored_from": target.id}, commit=False,
        )
        change = OntologyChange(
            id=str(__import__("uuid").uuid4()), ontology_id=ontology_id,
            base_revision_id=current.id if current else None, result_revision_id=revision.id,
            target_kind="ontology", operation="restore", target_id=target.id,
            before_json=before, after_json=restored_snapshot,
            impact_json={"restored_from": target.id}, validation_json={"ok": True}, status="applied",
        )
        db.add(change)
        db.commit(); db.refresh(revision)
    except ValueError as exc:
        db.rollback()
        raise HTTPException(409, str(exc))
    except Exception:
        db.rollback()
        raise
    # Restoring a revision changes the published schema, so it receives the
    # same asynchronous local audit as a normal editor save.  The audit is
    # queued after the revision transaction has committed and cannot roll the
    # restoration back if Ollama is unavailable.
    try:
        from app.services.v2.audit_runner import queue_local_audit
        queue_local_audit(db, ontology_id=ontology_id, revision_id=revision.id, construction_run_id=None)
    except Exception:
        pass
    return serialize_revision(revision)
