"""Persist text/OCR fragments produced by the existing media pipeline."""
from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.deps import get_current_user
from app.models.v2.dataset import MediaItem
from app.models.v2.multimodal import ExtractedFragment

router = APIRouter(prefix="/multimodal", dependencies=[Depends(get_current_user)])


def get_db():
    db = SessionLocal()
    try:
        yield db
    finally:
        db.close()


class FragmentCreate(BaseModel):
    media_item_id: str
    dataset_version_id: str
    fragment_type: str = Field(pattern="^(text|ocr|table|metadata)$")
    content: str
    locator: dict = {}
    extractor: str = Field(pattern="^(markitdown|ocr|rule|bridge_fallback)$")
    status: str = "completed"
    error: str | None = None


@router.post("/fragments")
def create_fragment(body: FragmentCreate, db: Session = Depends(get_db)):
    media = db.query(MediaItem).filter(MediaItem.id == body.media_item_id, MediaItem.dataset_version_id == body.dataset_version_id).first()
    if not media:
        raise HTTPException(404, "Media item or dataset version not found")
    fragment = ExtractedFragment(**body.model_dump())
    db.add(fragment)
    db.commit()
    db.refresh(fragment)
    return _serialize(fragment)


@router.get("/fragments/{media_item_id}")
def list_fragments(media_item_id: str, db: Session = Depends(get_db)):
    items = db.query(ExtractedFragment).filter(ExtractedFragment.media_item_id == media_item_id).order_by(ExtractedFragment.created_at.asc()).all()
    return {"media_item_id": media_item_id, "fragments": [_serialize(item) for item in items], "count": len(items)}


def _serialize(item: ExtractedFragment) -> dict:
    return {
        "id": item.id,
        "media_item_id": item.media_item_id,
        "dataset_version_id": item.dataset_version_id,
        "fragment_type": item.fragment_type,
        "content": item.content,
        "locator": item.locator or {},
        "extractor": item.extractor,
        "status": item.status,
        "error": item.error,
        "created_at": item.created_at.isoformat() if item.created_at else None,
    }
