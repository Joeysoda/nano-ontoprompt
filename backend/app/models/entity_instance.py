import uuid
from datetime import datetime, timezone
from sqlalchemy import String, DateTime, ForeignKey, JSON, event, text
from sqlalchemy.orm import Mapped, mapped_column
from app.database import Base

class EntityInstance(Base):
    """行级实例数据 — Pipeline Mapping 每行数据存为一条 instance"""
    __tablename__ = "entity_instances"

    id: Mapped[str] = mapped_column(String, primary_key=True, default=lambda: str(uuid.uuid4()))
    entity_id: Mapped[str] = mapped_column(String, ForeignKey("entities.id", ondelete="CASCADE"), nullable=False)
    ontology_id: Mapped[str] = mapped_column(String, ForeignKey("ontology_projects.id", ondelete="CASCADE"), nullable=False)
    row_identity: Mapped[str] = mapped_column(String(200), nullable=False, index=True)
    row_data: Mapped[dict] = mapped_column(JSON, default=dict)
    revision_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    # Stable canonical Object Type identity.  ``entity_id`` remains for the
    # legacy adapter while new readers can resolve instances without relying
    # on a mutable Entity row.
    object_type_resource_id: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    source_version: Mapped[str | None] = mapped_column(String(120), nullable=True)
    created_at: Mapped[datetime] = mapped_column(DateTime, default=lambda: datetime.now(timezone.utc))


def _reject_interface_instance(connection, target: EntityInstance) -> None:
    """Interfaces describe a contract; only concrete Object Types can own rows.

    ``entity_id`` remains the legacy foreign key, so the check resolves both
    the new stable resource pointer and the compatibility Entity row.  Keeping
    this at the persistence boundary prevents an import, live edit, or
    background materializer from accidentally creating an interface instance.
    """
    resource_id = target.object_type_resource_id
    kind = None
    if resource_id:
        kind = connection.execute(
            text("SELECT kind FROM v2_semantic_resources WHERE id = :resource_id"),
            {"resource_id": str(resource_id)},
        ).scalar()
    if kind is None and target.entity_id:
        kind = connection.execute(
            text(
                "SELECT r.kind FROM v2_semantic_resources r "
                "JOIN entities e ON e.semantic_resource_id = r.id "
                "WHERE e.id = :entity_id LIMIT 1"
            ),
            {"entity_id": str(target.entity_id)},
        ).scalar()
    if str(kind or "").lower() == "interface":
        raise ValueError("INTERFACE_NOT_INSTANTIABLE: Interface 不能创建 EntityInstance")


@event.listens_for(EntityInstance, "before_insert")
def _entity_instance_before_insert(mapper, connection, target):  # noqa: ARG001
    _reject_interface_instance(connection, target)


@event.listens_for(EntityInstance, "before_update")
def _entity_instance_before_update(mapper, connection, target):  # noqa: ARG001
    _reject_interface_instance(connection, target)
