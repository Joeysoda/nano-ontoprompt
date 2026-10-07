"""Shared object/property authorization projection.

Every terminal read should call this module once and pass the resulting
projection down to its adapter.  It intentionally supports a small,
structured condition language; arbitrary SQL and user-supplied expressions
are never evaluated.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Iterable

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.ontology import OntologyProject
from app.models.v2.semantic_core import OntologySemanticResourceVersion
from app.models.v2.security import OntologySecurityPolicy


class AuthorizationError(ValueError):
    def __init__(self, code: str, message: str, *, details: dict[str, Any] | None = None):
        super().__init__(message)
        self.code = code
        self.details = details or {}
        self.context_id = str(uuid.uuid4())


@dataclass(frozen=True)
class AuthorizationContext:
    ontology_id: str
    principal_id: str
    principal_role: str | None = None
    metadata_revision_id: str | None = None
    requested_fields: tuple[str, ...] = ()
    object_type_id: str | None = None
    # Optional resource scope used by link/property readers.  Object queries
    # may leave this at ``ontology`` and the projection will derive the
    # property/link scope from each materialized record.
    scope_kind: str = "ontology"
    scope_id: str | None = None


@dataclass
class AuthorizationProjection:
    context: AuthorizationContext
    allowed: bool
    visible_object_ids: set[str] | None = None
    allowed_fields: set[str] | None = None
    allowed_fields_by_object: dict[str, set[str] | None] = field(default_factory=dict)
    denied_fields: set[str] = field(default_factory=set)
    denied_fields_by_object: dict[str, set[str]] = field(default_factory=dict)
    policy_ids: list[str] = field(default_factory=list)
    permission_digest: str = ""
    reason: str | None = None
    # Keep the already-loaded policy set with the projection.  Readers that
    # stream rows can evaluate row conditions without issuing another query.
    object_allow_policies: list[OntologySecurityPolicy] = field(default_factory=list, repr=False)
    object_deny_policies: list[OntologySecurityPolicy] = field(default_factory=list, repr=False)
    field_allow_policies: list[OntologySecurityPolicy] = field(default_factory=list, repr=False)
    field_deny_policies: list[OntologySecurityPolicy] = field(default_factory=list, repr=False)
    link_allow_policies: list[OntologySecurityPolicy] = field(default_factory=list, repr=False)
    link_deny_policies: list[OntologySecurityPolicy] = field(default_factory=list, repr=False)
    # Stable resource IDs in policies resolve to API names in legacy row data.
    resource_aliases: dict[str, str] = field(default_factory=dict, repr=False)
    resource_ids_by_api_name: dict[str, str] = field(default_factory=dict, repr=False)

    @property
    def unrestricted(self) -> bool:
        return (
            self.allowed
            and self.visible_object_ids is None
            and self.allowed_fields is None
            and not self.denied_fields
            and not self.denied_fields_by_object
        )


def _subject_matches(policy: OntologySecurityPolicy, principal_id: str, principal_role: str | None) -> bool:
    if policy.subject_kind == "user":
        return policy.subject_id in {principal_id, "*"}
    if policy.subject_kind == "role":
        return policy.subject_id in {str(principal_role or ""), "*"}
    return False


def _value_for(record: dict[str, Any], field: str) -> Any:
    if field in record:
        return record.get(field)
    properties = record.get("properties")
    if isinstance(properties, dict):
        return properties.get(field)
    return None


def _condition_matches(record: dict[str, Any], condition: dict[str, Any], resource_aliases: dict[str, str] | None = None) -> bool:
    field = str(condition.get("field") or condition.get("property") or "")
    if not field:
        return False
    field = (resource_aliases or {}).get(field, field)
    actual = _value_for(record, field)
    operator = str(condition.get("operator") or condition.get("op") or "equals").lower()
    expected = condition.get("value")
    if operator in {"equals", "=", "=="}:
        return actual == expected
    if operator in {"not_equals", "!=", "≠"}:
        return actual != expected
    if operator in {"in", "belongs_to", "属于"}:
        return actual in (expected if isinstance(expected, (list, tuple, set)) else [expected])
    if operator in {"not_in"}:
        return actual not in (expected if isinstance(expected, (list, tuple, set)) else [expected])
    try:
        if operator in {">", "gt"}:
            return actual is not None and actual > expected
        if operator in {">=", "≥", "gte"}:
            return actual is not None and actual >= expected
        if operator in {"<", "lt"}:
            return actual is not None and actual < expected
        if operator in {"<=", "≤", "lte"}:
            return actual is not None and actual <= expected
        if operator in {"range", "between"}:
            if actual is None or not isinstance(expected, dict):
                return False
            if expected.get("min") is not None and actual < expected["min"]:
                return False
            if expected.get("max") is not None and actual > expected["max"]:
                return False
            return True
    except (TypeError, ValueError):
        return False
    return False


def conditions_match(record: dict[str, Any], conditions: Any, resource_aliases: dict[str, str] | None = None) -> bool:
    if not conditions:
        return True
    if isinstance(conditions, dict):
        conditions = conditions.get("all") or [conditions]
    if not isinstance(conditions, list):
        return False
    return all(isinstance(item, dict) and _condition_matches(record, item, resource_aliases) for item in conditions)


def _expanded_references(
    values: Iterable[Any],
    resource_aliases: dict[str, str] | None,
    resource_ids_by_api_name: dict[str, str] | None = None,
) -> set[str]:
    aliases = resource_aliases or {}
    reverse_aliases = resource_ids_by_api_name or {}
    expanded: set[str] = set()
    for value in values:
        text = str(value or "")
        if text:
            expanded.add(text)
            expanded.add(aliases.get(text, text))
            expanded.add(reverse_aliases.get(text, text))
    return expanded


def _scope_matches(
    policy: OntologySecurityPolicy,
    object_type_id: str | None,
    record: dict[str, Any] | None,
    context: AuthorizationContext | None = None,
    resource_aliases: dict[str, str] | None = None,
    resource_ids_by_api_name: dict[str, str] | None = None,
) -> bool:
    if policy.scope_kind == "ontology":
        return True
    if policy.scope_kind == "object_type":
        candidates = _expanded_references([object_type_id], resource_aliases, resource_ids_by_api_name)
        if record:
            candidates.update(_expanded_references([
                record.get("entity_id"), record.get("entity_type"),
                record.get("semantic_resource_id"), record.get("object_type_resource_id"),
            ], resource_aliases, resource_ids_by_api_name))
        return str(policy.scope_id or "") in candidates
    if policy.scope_kind == "property":
        candidates = _expanded_references([(context or AuthorizationContext("", "")).scope_id], resource_aliases, resource_ids_by_api_name)
        if record:
            properties = record.get("properties") if isinstance(record.get("properties"), dict) else {}
            candidates.update(str(key) for key in properties)
            candidates.update(str(key) for key in (record.get("property_ids") or []))
            candidates.update(str(key) for key in (record.get("fields") or []))
            reverse_aliases = resource_ids_by_api_name or {}
            candidates.update(reverse_aliases[key] for key in properties if key in reverse_aliases)
        return str(policy.scope_id or "") in candidates
    if policy.scope_kind == "link":
        candidates = _expanded_references([(context or AuthorizationContext("", "")).scope_id], resource_aliases, resource_ids_by_api_name)
        if record:
            candidates.update(_expanded_references(
                [record.get(key) for key in ("link_type_id", "link_id", "relation_type", "type")],
                resource_aliases,
                resource_ids_by_api_name,
            ))
        return str(policy.scope_id or "") in candidates
    return False


def _policy_field_names(policy: OntologySecurityPolicy, resource_aliases: dict[str, str] | None = None) -> set[str]:
    aliases = resource_aliases or {}
    fields = {aliases.get(str(item), str(item)) for item in (policy.field_allowlist_json or []) if item}
    # A property-scoped policy is itself a field selector when no explicit
    # allowlist is supplied.
    if not fields and policy.scope_kind == "property" and policy.scope_id:
        fields.add(aliases.get(str(policy.scope_id), str(policy.scope_id)))
    return fields


def _load_resource_aliases(
    db: Session,
    context: AuthorizationContext,
    project: OntologyProject,
) -> tuple[dict[str, str], dict[str, str]]:
    revision_id = context.metadata_revision_id or project.current_revision_id
    if not revision_id:
        return {}, {}
    rows = db.query(OntologySemanticResourceVersion.resource_id, OntologySemanticResourceVersion.api_name).filter(
        OntologySemanticResourceVersion.ontology_id == context.ontology_id,
        OntologySemanticResourceVersion.revision_id == revision_id,
    ).all()
    forward = {str(resource_id): str(api_name) for resource_id, api_name in rows if resource_id and api_name}
    return forward, {api_name: resource_id for resource_id, api_name in forward.items()}


def _policy_digest(policies: Iterable[OntologySecurityPolicy], context: AuthorizationContext) -> str:
    payload = {
        "context": context.__dict__,
        "policies": [
            {
                "id": row.id,
                "updated_at": row.updated_at.isoformat() if row.updated_at else None,
                "effect": row.effect,
                "scope": [row.scope_kind, row.scope_id],
                "conditions": row.conditions_json or [],
                "fields": row.field_allowlist_json or [],
            }
            for row in policies
        ],
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str).encode()).hexdigest()


def load_applicable_policies(
    db: Session,
    context: AuthorizationContext,
    principal_role: str | None = None,
) -> list[OntologySecurityPolicy]:
    role = principal_role or context.principal_role
    revision_id = context.metadata_revision_id or db.query(OntologyProject.current_revision_id).filter(
        OntologyProject.id == context.ontology_id,
    ).scalar()
    query = db.query(OntologySecurityPolicy).filter(
        OntologySecurityPolicy.ontology_id == context.ontology_id,
        OntologySecurityPolicy.enabled.is_(True),
    )
    if revision_id:
        query = query.filter(or_(
            OntologySecurityPolicy.revision_id.is_(None),
            OntologySecurityPolicy.revision_id == revision_id,
        ))
    return [row for row in query.order_by(
        OntologySecurityPolicy.priority.asc(), OntologySecurityPolicy.created_at.asc(),
    ).all() if _subject_matches(row, context.principal_id, role)]


def resolve_projection(
    db: Session,
    context: AuthorizationContext,
    principal_role: str | None = None,
    *,
    records: Iterable[dict[str, Any]] | None = None,
    preloaded_policies: list[OntologySecurityPolicy] | None = None,
) -> AuthorizationProjection:
    """Resolve one projection for a terminal operation.

    ``records`` is optional.  When supplied, object conditions are evaluated
    in one batch and visible IDs are returned; otherwise the caller can use
    ``redact_record`` as it materializes rows.
    """
    project = db.query(OntologyProject).filter(OntologyProject.id == context.ontology_id).first()
    if not project:
        raise AuthorizationError("NOT_FOUND", "本体不存在")
    role = principal_role or context.principal_role
    resource_aliases, resource_ids_by_api_name = _load_resource_aliases(db, context, project)
    policies = preloaded_policies if preloaded_policies is not None else load_applicable_policies(db, context, principal_role)
    digest = _policy_digest(policies, context)
    try:
        db.info.setdefault("plan_a_last_permission_digest", {})[(context.ontology_id, context.principal_id)] = digest
    except Exception:
        pass
    owner = str(project.created_by or "") == str(context.principal_id)
    admin = str(role or "") == "admin"
    if owner or admin:
        return AuthorizationProjection(context=context, allowed=True, allowed_fields=None, policy_ids=[row.id for row in policies], permission_digest=digest, reason="owner_or_admin")

    # Ontology/object_type policies decide whether an object is visible.
    # Property/link policies only shape the corresponding fields/edges and
    # must never grant object access by themselves.
    object_scope_kinds = {"ontology", "object_type"}
    if context.scope_kind == "link":
        object_scope_kinds.add("link")
    if context.scope_kind == "property":
        object_scope_kinds.add("property")
    object_scoped = [
        row for row in policies
        if row.scope_kind in object_scope_kinds
        and (
            _scope_matches(row, context.object_type_id, None, context, resource_aliases, resource_ids_by_api_name)
            or (row.scope_kind == "object_type" and context.object_type_id is None)
        )
    ]
    # Property/link scopes are resolved against each record below.  Keeping
    # them in the candidate set here is important: a property policy cannot
    # be matched against a record until its ``properties`` keys are known.
    field_scoped = [row for row in policies if row.scope_kind == "property"]
    link_scoped = [row for row in policies if row.scope_kind == "link"]
    scoped = object_scoped + field_scoped + link_scoped
    allow = [row for row in object_scoped if str(row.effect).lower() == "allow"]
    deny = [row for row in object_scoped if str(row.effect).lower() == "deny"]
    field_allow = [row for row in field_scoped if str(row.effect).lower() == "allow"]
    field_deny = [row for row in field_scoped if str(row.effect).lower() == "deny"]
    link_allow = [row for row in link_scoped if str(row.effect).lower() == "allow"]
    link_deny = [row for row in link_scoped if str(row.effect).lower() == "deny"]
    allowed = bool(allow)
    field_allowlist: set[str] | None = None
    allowed_fields_by_object: dict[str, set[str] | None] = {}
    denied_fields: set[str] = set()
    denied_fields_by_object: dict[str, set[str]] = {}
    for row in allow:
        fields = _policy_field_names(row, resource_aliases)
        if fields:
            field_allowlist = fields if field_allowlist is None else field_allowlist | fields
    for row in field_allow:
        fields = _policy_field_names(row, resource_aliases)
        if fields:
            field_allowlist = fields if field_allowlist is None else field_allowlist | fields
    for row in [*deny, *field_deny]:
        # An empty deny field list is an object-level deny.  A non-empty list
        # is a column-level deny and only applies when that policy's
        # structured conditions match the record.
        fields = _policy_field_names(row, resource_aliases)
        if not fields:
            continue
        # Without materialized records we cannot evaluate a conditional
        # policy, so conservatively mask that field.  With records, the
        # object-specific map below narrows it back to matching rows.
        if not row.conditions_json:
            denied_fields.update(fields)
    requested = {resource_aliases.get(str(item), str(item)) for item in context.requested_fields}
    if requested and field_allowlist is not None:
        allowed_fields = requested & field_allowlist
    else:
        allowed_fields = field_allowlist
    if allowed_fields is not None:
        allowed_fields -= denied_fields
    visible_ids: set[str] | None = None
    if records is not None:
        records_list = list(records)
        visible_ids = set()
        for record in records_list:
            object_id = str(record.get("id") or record.get("object_id") or record.get("entity_instance_id") or "")
            matching_allow_rows = [row for row in allow if conditions_match(record, row.conditions_json, resource_aliases) and _scope_matches(row, context.object_type_id, record, context, resource_aliases, resource_ids_by_api_name)]
            matching_allow = bool(matching_allow_rows)
            matching_field_allow_rows = [row for row in field_allow if conditions_match(record, row.conditions_json, resource_aliases) and _scope_matches(row, context.object_type_id, record, context, resource_aliases, resource_ids_by_api_name)]
            row_allow_fields = {field for row in matching_allow_rows for field in _policy_field_names(row, resource_aliases)} | {field for row in matching_field_allow_rows for field in _policy_field_names(row, resource_aliases)}
            if matching_allow:
                # A matching allow without a field list grants the full
                # record; otherwise only the fields from matching policies
                # are visible for this specific object.
                projection_fields = None if any(not _policy_field_names(row, resource_aliases) for row in matching_allow_rows) else row_allow_fields
                allowed_fields_by_object[object_id] = projection_fields
            object_denies = [
                row for row in deny
                if not _policy_field_names(row, resource_aliases)
                and conditions_match(record, row.conditions_json, resource_aliases)
                and _scope_matches(row, context.object_type_id, record, context, resource_aliases, resource_ids_by_api_name)
            ]
            field_denies = {field for row in [*deny, *field_deny] if _policy_field_names(row, resource_aliases) and conditions_match(record, row.conditions_json, resource_aliases) and _scope_matches(row, context.object_type_id, record, context, resource_aliases, resource_ids_by_api_name) for field in _policy_field_names(row, resource_aliases)}
            if field_denies:
                denied_fields_by_object[object_id] = field_denies
            if matching_allow and not object_denies:
                visible_ids.add(object_id)
        allowed = allowed and bool(visible_ids or not records_list)
    projection = AuthorizationProjection(
        context=context, allowed=allowed, visible_object_ids=visible_ids,
        allowed_fields=allowed_fields, allowed_fields_by_object=allowed_fields_by_object,
        denied_fields=denied_fields, denied_fields_by_object=denied_fields_by_object,
        policy_ids=[row.id for row in scoped], permission_digest=digest,
        reason="policy_match" if allowed else "no_matching_policy",
        object_allow_policies=allow,
        object_deny_policies=deny,
        field_allow_policies=field_allow,
        field_deny_policies=field_deny,
        link_allow_policies=link_allow,
        link_deny_policies=link_deny,
        resource_aliases=resource_aliases,
        resource_ids_by_api_name=resource_ids_by_api_name,
    )
    return projection


def redact_record(projection: AuthorizationProjection, record: dict[str, Any]) -> dict[str, Any] | None:
    object_id = str(record.get("id") or record.get("object_id") or record.get("entity_instance_id") or "")
    if not projection.allowed:
        return None
    if projection.visible_object_ids is not None and object_id not in projection.visible_object_ids:
        return None
    if projection.unrestricted:
        return dict(record)

    # When the adapter did not materialize the complete row set up front,
    # enforce conditional object and field policies here against this row.
    # Previously ``allowed`` meant that some allow rule existed, which let a
    # conditional allow accidentally expose rows that did not match it.
    if projection.visible_object_ids is None and projection.object_allow_policies:
        matching_allows = [
            policy for policy in projection.object_allow_policies
            if conditions_match(record, policy.conditions_json, projection.resource_aliases)
            and _scope_matches(policy, projection.context.object_type_id, record, projection.context, projection.resource_aliases, projection.resource_ids_by_api_name)
        ]
        if not matching_allows:
            return None
        matching_denies = [
            policy for policy in projection.object_deny_policies
            if conditions_match(record, policy.conditions_json, projection.resource_aliases)
            and _scope_matches(policy, projection.context.object_type_id, record, projection.context, projection.resource_aliases, projection.resource_ids_by_api_name)
        ]
        matching_field_allows = [
            policy for policy in projection.field_allow_policies
            if conditions_match(record, policy.conditions_json, projection.resource_aliases)
            and _scope_matches(policy, projection.context.object_type_id, record, projection.context, projection.resource_aliases, projection.resource_ids_by_api_name)
        ]
        matching_field_denies = [
            policy for policy in projection.field_deny_policies
            if conditions_match(record, policy.conditions_json, projection.resource_aliases)
            and _scope_matches(policy, projection.context.object_type_id, record, projection.context, projection.resource_aliases, projection.resource_ids_by_api_name)
        ]
        named_allows = [*matching_allows, *matching_field_allows]
        if any(not _policy_field_names(policy, projection.resource_aliases) for policy in matching_allows):
            row_allowed_fields = None
        else:
            row_allowed_fields = {name for policy in named_allows for name in _policy_field_names(policy, projection.resource_aliases)}
        requested = set(projection.context.requested_fields)
        requested = {projection.resource_aliases.get(str(item), str(item)) for item in requested}
        if row_allowed_fields is not None and requested:
            row_allowed_fields &= requested
        row_denied_fields = {
            name for policy in [*matching_denies, *matching_field_denies]
            for name in _policy_field_names(policy, projection.resource_aliases)
        }
        object_level_deny = any(not _policy_field_names(policy, projection.resource_aliases) for policy in matching_denies)
        if object_level_deny:
            return None
    else:
        row_allowed_fields = projection.allowed_fields_by_object.get(object_id, projection.allowed_fields)
        row_denied_fields = projection.denied_fields | projection.denied_fields_by_object.get(object_id, set())

    result = dict(record)
    record_allowed_fields = row_allowed_fields
    if record_allowed_fields is not None:
        for key in list(result):
            if key in {"id", "object_id", "entity_instance_id", "entity_type", "entity_id", "properties"}:
                continue
            if key not in record_allowed_fields:
                result[key] = None
        properties = result.get("properties")
        if isinstance(properties, dict):
            result["properties"] = {key: (value if key in record_allowed_fields else None) for key, value in properties.items()}
    for key in row_denied_fields:
        if key in result:
            result[key] = None
        if isinstance(result.get("properties"), dict) and key in result["properties"]:
            result["properties"][key] = None
    return result


def filter_records(projection: AuthorizationProjection, records: Iterable[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for record in records if (item := redact_record(projection, record)) is not None]


def require_visible(projection: AuthorizationProjection, record: dict[str, Any]) -> dict[str, Any]:
    visible = redact_record(projection, record)
    if visible is None:
        raise AuthorizationError("NOT_FOUND", "对象不存在")
    return visible


def redact_link_record(
    projection: AuthorizationProjection,
    record: dict[str, Any],
    *,
    link_type_id: str,
) -> dict[str, Any] | None:
    """Apply compiled link policies without loading policies per edge."""
    if projection.unrestricted:
        return dict(record)
    context = AuthorizationContext(
        ontology_id=projection.context.ontology_id,
        principal_id=projection.context.principal_id,
        principal_role=projection.context.principal_role,
        metadata_revision_id=projection.context.metadata_revision_id,
        requested_fields=projection.context.requested_fields,
        scope_kind="link",
        scope_id=link_type_id,
    )
    scoped = [
        policy for policy in [*projection.link_allow_policies, *projection.link_deny_policies]
        if _scope_matches(policy, None, record, context, projection.resource_aliases, projection.resource_ids_by_api_name)
        and conditions_match(record, policy.conditions_json, projection.resource_aliases)
    ]
    allows = [policy for policy in scoped if str(policy.effect).lower() == "allow"]
    denies = [policy for policy in scoped if str(policy.effect).lower() == "deny"]
    if any(not _policy_field_names(policy, projection.resource_aliases) for policy in denies):
        return None
    # A field-only allow grants access to named edge properties, not to the
    # existence of the relationship itself.  Relationships are default-deny
    # for ordinary principals just like objects; endpoint visibility alone
    # must not reveal an otherwise unauthorized edge.
    if not any(not _policy_field_names(policy, projection.resource_aliases) for policy in allows):
        return None
    if allows and any(not _policy_field_names(policy, projection.resource_aliases) for policy in allows):
        allowed_fields = None
    elif allows:
        allowed_fields = {name for policy in allows for name in _policy_field_names(policy, projection.resource_aliases)}
    else:
        # A field-only deny masks link properties but does not hide the edge.
        allowed_fields = None
    denied_fields = {name for policy in denies for name in _policy_field_names(policy, projection.resource_aliases)}
    result = dict(record)
    properties = result.get("properties")
    if isinstance(properties, dict):
        if allowed_fields is not None:
            properties = {key: (value if key in allowed_fields else None) for key, value in properties.items()}
        for name in denied_fields:
            if name in properties:
                properties[name] = None
        result["properties"] = properties
    return result


def cached_permission_digest(db: Session, ontology_id: str, principal_id: str) -> str | None:
    cache = getattr(db, "info", {}).get("plan_a_last_permission_digest", {})
    return cache.get((ontology_id, principal_id))


__all__ = [
    "AuthorizationContext",
    "AuthorizationProjection",
    "AuthorizationError",
    "conditions_match",
    "resolve_projection",
    "load_applicable_policies",
    "redact_record",
    "filter_records",
    "require_visible",
    "redact_link_record",
    "cached_permission_digest",
]
