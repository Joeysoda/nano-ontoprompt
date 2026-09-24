"""Persistent Object Set definitions, ACLs, dependencies and execution leases."""
from datetime import datetime, timezone
from uuid import uuid4
from sqlalchemy import Column, String, Integer, JSON, Text, DateTime, ForeignKey, ForeignKeyConstraint, CheckConstraint, event
from app.database import Base


def now():
    return datetime.now(timezone.utc)


class ObjectSetResource(Base):
    __tablename__ = 'v2_object_set_resources'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    ontology_id = Column(String, ForeignKey('ontology_projects.id'), nullable=False, index=True)
    owner_id = Column(String, ForeignKey('users.id'), nullable=False, index=True)
    name = Column(String(200), nullable=False)
    description = Column(Text, nullable=False, default='')
    lifecycle = Column(String(20), nullable=False, default='permanent')
    definition_kind = Column(String(20), nullable=False, default='dynamic')
    head_version = Column(Integer, nullable=False, default=1)
    etag = Column(Integer, nullable=False, default=1)
    status = Column(String(20), nullable=False, default='active')
    expires_at = Column(DateTime(timezone=True), nullable=True)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)
    updated_at = Column(DateTime(timezone=True), nullable=False, default=now)
    __table_args__ = (
        CheckConstraint("status IN ('active','archived','tombstone')", name='ck_object_set_status'),
        CheckConstraint("lifecycle IN ('permanent','temporary')", name='ck_object_set_lifecycle'),
        CheckConstraint('head_version >= 1 AND etag >= 1', name='ck_object_set_versions'),
    )


class ObjectSetVersion(Base):
    __tablename__ = 'v2_object_set_versions'
    resource_id = Column(String(36), ForeignKey('v2_object_set_resources.id'), primary_key=True)
    version = Column(Integer, primary_key=True)
    contract_version = Column(String(30), nullable=False, default='contract_v2')
    definition = Column(JSON, nullable=False)
    result_type = Column(JSON, nullable=False)
    definition_hash = Column(String(64), nullable=False)
    metadata_digest = Column(String(64), nullable=False)
    dependency_manifest = Column(JSON, nullable=False, default=list)
    created_by = Column(String, ForeignKey('users.id'), nullable=False)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class ObjectSetStaticMember(Base):
    __tablename__ = 'v2_object_set_static_members'
    resource_id = Column(String(36), primary_key=True)
    version = Column(Integer, primary_key=True)
    concrete_type = Column(String(200), primary_key=True)
    object_id = Column(String(300), primary_key=True)
    __table_args__ = (ForeignKeyConstraint(['resource_id', 'version'], ['v2_object_set_versions.resource_id', 'v2_object_set_versions.version']),)


class ObjectSetGrant(Base):
    __tablename__ = 'v2_object_set_grants'
    resource_id = Column(String(36), ForeignKey('v2_object_set_resources.id'), primary_key=True)
    principal_id = Column(String, ForeignKey('users.id'), primary_key=True)
    role = Column(String(10), nullable=False)
    __table_args__ = (CheckConstraint("role IN ('view','edit')", name='ck_object_set_grant_role'),)


class ObjectSetAudit(Base):
    __tablename__ = 'v2_object_set_audits'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resource_id = Column(String(36), ForeignKey('v2_object_set_resources.id'), nullable=False, index=True)
    version = Column(Integer, nullable=False)
    actor_id = Column(String, nullable=False)
    operation = Column(String(40), nullable=False)
    definition_hash = Column(String(64), nullable=False)
    summary = Column(JSON, nullable=False, default=dict)
    created_at = Column(DateTime(timezone=True), nullable=False, default=now)


class ObjectSetDependency(Base):
    __tablename__ = 'v2_object_set_dependencies'
    resource_id = Column(String(36), primary_key=True)
    version = Column(Integer, primary_key=True)
    target_id = Column(String(36), primary_key=True)
    target_version = Column(Integer, primary_key=True)
    __table_args__ = (
        ForeignKeyConstraint(['resource_id', 'version'], ['v2_object_set_versions.resource_id', 'v2_object_set_versions.version']),
        ForeignKeyConstraint(['target_id', 'target_version'], ['v2_object_set_versions.resource_id', 'v2_object_set_versions.version']),
    )


class ObjectSetLease(Base):
    __tablename__ = 'v2_object_set_leases'
    id = Column(String(36), primary_key=True, default=lambda: str(uuid4()))
    resource_id = Column(String(36), nullable=False)
    version = Column(Integer, nullable=False)
    principal_id = Column(String, ForeignKey('users.id'), nullable=False)
    manifest = Column(JSON, nullable=False)
    expires_at = Column(DateTime(timezone=True), nullable=False, index=True)
    __table_args__ = (ForeignKeyConstraint(['resource_id', 'version'], ['v2_object_set_versions.resource_id', 'v2_object_set_versions.version']),)


@event.listens_for(ObjectSetVersion, 'before_update')
def _immutable_version(mapper, connection, target):
    raise ValueError('Published Object Set versions are immutable')
