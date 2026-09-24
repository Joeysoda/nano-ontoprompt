"""Transactional Object Set resources. Grants never bypass ontology access."""
from copy import deepcopy
from datetime import datetime, timedelta, timezone
from uuid import uuid4
from pydantic import TypeAdapter
from sqlalchemy import update, or_, select
from app.models.ontology import OntologyProject
from app.models.user import User
from app.models.v2.object_set import (ObjectSetResource as Resource, ObjectSetVersion as Version,
    ObjectSetStaticMember as Member, ObjectSetGrant as Grant, ObjectSetAudit as Audit,
    ObjectSetDependency as Dependency, ObjectSetLease as Lease, now)
from app.schemas.v2.object_query import ObjectSetExpr, ParameterRef, ParameterSpec
from .core import metadata_digest
from .errors import ObjectQueryError
from .normalize import canonical_data, definition_hash, stable_hash
from .validate import validate_object_set
from .values import validate_parameter


def fail(code, message):
    raise ObjectQueryError(code, 'resource', message)


def aware(value):
    return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


class ResourceService:
    def __init__(self, db, ontology_id, user, metadata):
        self.db, self.ontology_id, self.user, self.metadata = db, ontology_id, user, metadata
        self.parameter_schema = {}

    def authorize_ontology(self):
        ontology = self.db.get(OntologyProject, self.ontology_id)
        if ontology is None or (self.user.role != 'admin' and ontology.created_by != self.user.id):
            fail('not_found', 'Ontology not found or inaccessible')

    def get(self, resource_id, edit=False, owner=False, allow_deleted=False):
        self.authorize_ontology()
        resource = self.db.get(Resource, resource_id, populate_existing=True)
        if resource is None or resource.ontology_id != self.ontology_id:
            fail('not_found', 'Resource not found or inaccessible')
        privileged = self.user.role == 'admin' or resource.owner_id == self.user.id
        grant = self.db.get(Grant, (resource_id, self.user.id))
        if not privileged and grant is None:
            fail('not_found', 'Resource not found or inaccessible')
        if not privileged and (owner or (edit and grant.role != 'edit')):
            fail('forbidden', 'Resource operation is forbidden')
        if resource.status == 'tombstone' and not allow_deleted:
            fail('not_found', 'Resource has been deleted')
        if resource.expires_at and aware(resource.expires_at) <= now() and not allow_deleted:
            fail('expired_reference', 'Resource has expired')
        return resource

    def version(self, resource_id, version=None):
        resource = self.get(resource_id)
        row = self.db.get(Version, (resource_id, version or resource.head_version))
        if row is None:
            fail('not_found', 'Definition version not found')
        return row

    def expand(self, expression, forbid_id=None):
        manifest = {}
        self.parameter_schema = {}
        budget = 0
        def walk(value, stack=()):
            nonlocal budget
            if isinstance(value, list): return [walk(v, stack) for v in value]
            if not isinstance(value, dict): return deepcopy(value)
            budget += 1
            if budget > 5000:
                fail('query_too_complex', 'Reference closure exceeds budget')
            if value.get('kind') == 'reference':
                rid = value['object_set_id']
                if rid in stack or rid == forbid_id:
                    fail('reference_cycle', 'Reference cycle detected')
                if len(stack) >= 16:
                    fail('query_too_complex', 'Reference depth exceeds 16')
                if not value.get('definition_version'):
                    fail('invalid_definition', 'References must pin definition_version')
                resource = self.get(rid)
                if resource.status != 'active': fail('stale_reference', 'Resource is archived')
                row = self.version(rid, value['definition_version'])
                if row.metadata_digest != metadata_digest(self.metadata):
                    fail('metadata_mismatch', 'Referenced metadata has changed')
                manifest[(rid, row.version)] = {'resource_id': rid, 'version': row.version, 'definition_hash': row.definition_hash}
                for name, spec in row.definition.get('parameter_schema', {}).items():
                    if name in self.parameter_schema and self.parameter_schema[name] != spec:
                        fail('invalid_definition', 'Conflicting parameter declarations in references')
                    self.parameter_schema[name] = deepcopy(spec)
                return walk(row.definition['expression'], (*stack, rid))
            return {k: walk(v, stack) for k, v in value.items()}
        resolved = TypeAdapter(ObjectSetExpr).validate_python(walk(canonical_data(expression)))
        return resolved, [manifest[k] for k in sorted(manifest)]

    def prepare(self, definition, forbid_id=None):
        definition = definition.model_copy(deep=True)
        resolved, manifest = self.expand(definition.expression, forbid_id)
        schema = {k: v.model_dump(mode='python', exclude_unset=True) for k, v in definition.parameter_schema.items()}
        for name, spec in self.parameter_schema.items():
            if name in schema and schema[name] != spec:
                fail('invalid_definition', 'Conflicting parameter declarations')
            schema[name] = spec
        parsed_schema = {k: ParameterSpec.model_validate(v) for k, v in schema.items()}
        for spec in parsed_schema.values():
            try:
                if 'default' in spec.model_fields_set: validate_parameter(spec.default, spec)
                if spec.data_type == 'array' and spec.element_type is None:
                    raise ValueError('Array requires element_type')
                if spec.minimum is not None and spec.maximum is not None:
                    from .values import typed
                    if typed(spec.minimum, spec.data_type) > typed(spec.maximum, spec.data_type):
                        raise ValueError('Invalid parameter range')
            except (ValueError, TypeError, ArithmeticError) as exc:
                fail('invalid_parameter', str(exc))
        def check(value, prop=None):
            if isinstance(value, list):
                for v in value: check(v, prop)
            elif isinstance(value, dict):
                if value.get('kind') == 'parameter':
                    ref = ParameterRef.model_validate(value)
                    spec = parsed_schema.get(ref.name)
                    if spec is None or ref.data_type != spec.data_type or prop not in spec.allowed_fields:
                        fail('invalid_parameter', 'Parameter declaration or field permission mismatch')
                for v in value.values(): check(v, value.get('property', {}).get('api_name', prop))
        check(resolved.model_dump(mode='python'))
        validation = validate_object_set(resolved, self.metadata)
        canonical = {'contract_version': 'contract_v2', 'expression': canonical_data(definition.expression), 'parameter_schema': schema}
        return canonical, validation, manifest

    def _publish(self, resource, definition):
        canonical, validation, manifest = self.prepare(definition, resource.id)
        row = Version(resource_id=resource.id, version=resource.head_version, definition=canonical,
            result_type=validation.result_type.model_dump(),
            definition_hash=definition_hash(definition.expression, canonical['parameter_schema']),
            metadata_digest=metadata_digest(self.metadata), dependency_manifest=manifest, created_by=self.user.id)
        self.db.add(row)
        self.db.flush()
        for item in manifest:
            self.db.add(Dependency(resource_id=resource.id, version=row.version,
                target_id=item['resource_id'], target_version=item['version']))
        def members(node):
            if isinstance(node, list):
                for v in node: yield from members(v)
            elif isinstance(node, dict):
                if node.get('kind') == 'static':
                    for oid in node['object_ids']: yield node['type_ref']['api_name'], oid
                for v in node.values(): yield from members(v)
        for typ, oid in set(members(canonical['expression'])):
            self.db.add(Member(resource_id=resource.id, version=row.version, concrete_type=typ, object_id=oid))
        self.audit(resource, 'publish', row.definition_hash)
        return row

    def audit(self, resource, operation, digest=None):
        if digest is None:
            digest = self.db.get(Version, (resource.id, resource.head_version)).definition_hash
        self.db.add(Audit(resource_id=resource.id, version=resource.head_version, actor_id=self.user.id,
            operation=operation, definition_hash=digest, summary={'etag': resource.etag}))

    def create(self, request):
        self.authorize_ontology()
        resource = Resource(id=str(uuid4()), ontology_id=self.ontology_id, owner_id=self.user.id,
            name=request.name, description=request.description, lifecycle=request.lifecycle,
            definition_kind='static' if request.definition.expression.kind in ('static', 'empty') else 'dynamic',
            expires_at=now() + timedelta(seconds=request.ttl_seconds) if request.lifecycle == 'temporary' else None,
            head_version=1, etag=1, status='active')
        self.db.add(resource)
        self.db.flush()
        self._publish(resource, request.definition)
        self.db.commit()
        return resource

    def new_version(self, rid, request):
        resource = self.get(rid, edit=True)
        self.prepare(request.definition, rid)
        result = self.db.execute(update(Resource).where(Resource.id == rid, Resource.head_version == request.expected_version,
            Resource.etag == resource.etag, Resource.status != 'tombstone').values(
                head_version=request.expected_version + 1, etag=Resource.etag + 1, updated_at=now()), execution_options={'synchronize_session': False})
        if result.rowcount != 1:
            self.db.rollback()
            fail('version_conflict', 'Definition changed; reload the resource')
        self.db.refresh(resource)
        self._publish(resource, request.definition)
        self.db.commit()
        return resource

    def mutate(self, rid, expected_etag, changes, operation, owner=False):
        resource = self.get(rid, edit=True, owner=owner, allow_deleted=operation == 'tombstone')
        if resource.status == 'tombstone' and operation == 'tombstone': return resource
        result = self.db.execute(update(Resource).where(Resource.id == rid, Resource.etag == expected_etag).values(
            **changes, etag=Resource.etag + 1, updated_at=now()), execution_options={'synchronize_session': False})
        if result.rowcount != 1:
            self.db.rollback()
            fail('version_conflict', 'Resource changed; reload the resource')
        self.db.refresh(resource)
        self.audit(resource, operation)
        return resource

    def grant(self, rid, request):
        if self.db.get(User, request.principal_id) is None: fail('not_found', 'Principal not found')
        resource = self.mutate(rid, request.expected_etag, {}, 'grant', owner=True)
        existing = self.db.get(Grant, (rid, request.principal_id))
        if request.role is None:
            if existing: self.db.delete(existing)
        elif existing:
            existing.role = request.role
        else:
            self.db.add(Grant(resource_id=rid, principal_id=request.principal_id, role=request.role))
        self.db.commit()
        return resource

    def list(self, offset=0, limit=50):
        self.authorize_ontology()
        query = self.db.query(Resource).filter(Resource.ontology_id == self.ontology_id, Resource.status != 'tombstone')
        if self.user.role != 'admin':
            query = query.filter(or_(Resource.owner_id == self.user.id, Resource.id.in_(select(Grant.resource_id).where(Grant.principal_id == self.user.id))))
        return query.order_by(Resource.created_at, Resource.id).offset(offset).limit(limit).all()

    def lease(self, rid, version, seconds=300):
        row = self.version(rid, version)
        from app.schemas.v2.object_query import ReferenceObjectSet
        expression, references = self.expand(ReferenceObjectSet(kind='reference', object_set_id=rid, definition_version=version))
        lease = Lease(resource_id=rid, version=version, principal_id=self.user.id,
            manifest={'expression': canonical_data(expression), 'references': references,
                      'definition_hash': row.definition_hash, 'metadata_digest': row.metadata_digest},
            expires_at=now() + timedelta(seconds=min(seconds, 300)))
        self.db.add(lease)
        self.db.commit()
        return lease

    def read_lease(self, lease_id):
        self.authorize_ontology()
        lease = self.db.get(Lease, lease_id)
        if lease is None or lease.principal_id != self.user.id: fail('not_found', 'Lease not found')
        if aware(lease.expires_at) <= now(): fail('expired_reference', 'Lease expired')
        for item in lease.manifest['references']:
            # Tombstone/TTL stop new resolutions; existing manifest survives.
            # Grants and ontology authorization are checked again on each read.
            self.get(item['resource_id'], allow_deleted=True)
        if lease.manifest['metadata_digest'] != metadata_digest(self.metadata):
            fail('metadata_mismatch', 'Lease metadata is stale')
        return deepcopy(lease.manifest)
