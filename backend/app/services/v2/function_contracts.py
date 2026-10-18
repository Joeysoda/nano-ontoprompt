"""Function version, canonical reference and edit provenance contracts.

JSON Schema owns scalar/struct validation. Domain annotations delegate identity
to the same ontology catalog and object snapshot that Actions consume.
"""
from __future__ import annotations

import re
from app.services.v2.logic_contracts import ContractValidationError
from app.schemas.v2.scenario import Edit

RELEASE = re.compile(r"^(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)$")


def fail(stage, message):
    raise ContractValidationError(stage, [{"path": [], "message": message}])


def version_tuple(version):
    match = RELEASE.fullmatch(str(version))
    if not match:
        fail('version', 'Version must be a stable major.minor.patch release')
    return tuple(map(int, match.groups()))


def matches_version(version, requirement):
    value = version_tuple(version)
    prefix = requirement[:1]
    lower = version_tuple(requirement[1:] if prefix in {'^', '~'} else requirement)
    if prefix == '~':
        return lower <= value < (lower[0], lower[1] + 1, 0)
    if prefix == '^':
        upper = (lower[0] + 1, 0, 0) if lower[0] else ((0, lower[1] + 1, 0) if lower[1] else (0, 0, lower[2] + 1))
        return lower <= value < upper
    return value == lower


def resolve_version(assets, requirement):
    candidates = [asset for asset in assets if asset.status == 'published' and matches_version(asset.version, requirement)]
    if not candidates:
        fail('version', 'No published Function satisfies the version requirement')
    versions = [asset.version for asset in candidates]
    if len(set(versions)) != len(versions):
        fail('version', 'Ambiguous Function version')
    return max(candidates, key=lambda asset: version_tuple(asset.version))


def validate_domain(schema, value, *, metadata=None, snapshot=None, ontology_id=None):
    annotation = schema.get('x-ontology')
    if annotation:
        if metadata is None or snapshot is None or not ontology_id:
            fail('domain', 'Typed Function references require an authorized execution context')
        kind = annotation.get('kind')
        if kind == 'object':
            if not isinstance(value, dict) or set(value) != {'object_type', 'object_id'}:
                fail('domain', 'Object argument must be an object reference')
            metadata.resolve_type('object', value['object_type'], 'function.input')
            if annotation.get('api_name') and value['object_type'] != annotation['api_name']:
                fail('domain', 'Object reference has the wrong canonical type')
            if (value['object_type'], value['object_id']) not in snapshot.objects:
                fail('domain', 'Object reference is not visible in this execution context')
        elif kind == 'object_set':
            if not isinstance(value, dict) or value.get('kind') != 'set_handle' or value.get('completeness') != 'complete' or value.get('consistency') != 'snapshot' or not value.get('data_view_id'):
                fail('domain', 'Object Set arguments require a complete pinned SetHandle')
            if not isinstance(value.get('objects'), list) or len(value['objects']) > 10000:
                fail('domain', 'Invalid or oversized SetHandle')
            for item in value['objects']:
                validate_domain({'x-ontology': {'kind': 'object', 'api_name': annotation.get('api_name')}},
                                {key: item.get(key) for key in ('object_type', 'object_id')},
                                metadata=metadata, snapshot=snapshot, ontology_id=ontology_id)
        else:
            fail('domain', 'Unsupported domain annotation; a canonical adapter is required')
    if isinstance(value, dict):
        for key, child in schema.get('properties', {}).items():
            if key in value:
                validate_domain(child, value[key], metadata=metadata, snapshot=snapshot, ontology_id=ontology_id)
    if isinstance(value, list):
        for item in value:
            validate_domain(schema.get('items', {}), item, metadata=metadata, snapshot=snapshot, ontology_id=ontology_id)


class SolverOutcome(Exception):
    """An edit Function's solver finished without a usable plan; no edits exist."""

    def __init__(self, manifest):
        self.manifest = manifest
        super().__init__(f"Solver finished with status {manifest.get('status')}: {manifest.get('message', '')}")


def hydrate_domain(schema, value, *, snapshot, ontology_id):
    """Replace caller-supplied Object Set member data with the execution snapshot.

    SetHandle validation only proves membership; property values used for
    computation must come from the same pinned context, never from the caller.
    """
    annotation = schema.get('x-ontology') or {}
    if annotation.get('kind') == 'object_set' and isinstance(value, dict):
        objects = [{'object_type': item['object_type'], 'object_id': item['object_id'],
                    'properties': dict(snapshot.objects[(item['object_type'], item['object_id'])])} for item in value['objects']]
        return {**value, 'objects': objects, 'ontology_id': ontology_id}
    if isinstance(value, dict):
        return {key: hydrate_domain(schema.get('properties', {}).get(key, {}), child, snapshot=snapshot, ontology_id=ontology_id)
                for key, child in value.items()}
    if isinstance(value, list):
        return [hydrate_domain(schema.get('items', {}), item, snapshot=snapshot, ontology_id=ontology_id) for item in value]
    return value


def execution_class(asset):
    kind = (asset.get('config') or {}).get('execution_class', 'read')
    if kind not in {'read', 'edit'}:
        fail('execution', 'Unknown Function execution class')
    if asset.get('side_effect'):
        fail('execution', 'External side-effect Functions are not enabled')
    return kind


def validate_edits(asset, output, ontology_id):
    config = asset.get('config') or {}
    provenance = config.get('edit_provenance') or {}
    if not provenance or not isinstance(output, dict) or not isinstance(output.get('edits'), list) or not 1 <= len(output['edits']) <= 500:
        fail('edit', 'Edit Function must declare provenance and return a bounded edit batch')
    edits = [Edit.model_validate(item) for item in output['edits']]
    if [edit.sequence for edit in edits] != list(range(len(edits))):
        fail('edit', 'Function edits must have contiguous ordered sequence values')
    for edit in edits:
        targets = [edit.target] if edit.target else []
        if edit.link:
            targets.extend([edit.link.source, edit.link.target])
            if edit.link.ontology_id != ontology_id:
                fail('edit', 'Function link crosses ontology boundary')
        if not targets or edit.op == 'invoke_action':
            fail('edit', 'Function must return ontology edits, not nested Actions')
        for target in targets:
            if target.ontology_id != ontology_id or edit.op not in provenance.get(target.concrete_type, []):
                fail('edit', 'Function edit exceeds declared provenance')
    return edits
