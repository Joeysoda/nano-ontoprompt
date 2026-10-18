"""Fail closed until complete snapshot-backed SetHandles are executable."""
from .errors import ObjectQueryError


def reject_page_binding(asset, inputs):
    binding = asset.get('binding_spec') or {}
    if binding.get('kind') in ('set_handle', 'materialized_objects', 'object_set') or binding.get('requires_complete_set'):
        if isinstance(inputs, dict) and any(isinstance(value, dict) and value.get('kind') == 'set_handle' and value.get('completeness') == 'complete' and value.get('consistency') == 'snapshot' for value in inputs.values()):
            pass
        else:
            raise ObjectQueryError('complete_set_unavailable', 'binding',
                'This Logic runtime requires a complete snapshot SetHandle')
    def walk(value):
        if isinstance(value, dict):
            complete_handle = value.get('kind') == 'set_handle' and value.get('completeness') == 'complete' and value.get('consistency') == 'snapshot'
            # A complete snapshot SetHandle is executable: Function execution
            # re-hydrates its members from the pinned context. Query pages are not.
            if 'objects' in value and ('page' in value or 'completeness' in value) and not complete_handle:
                raise ObjectQueryError('complete_set_unavailable', 'inputs', 'Query pages are not complete Logic inputs')
            if value.get('kind') == 'set_handle' and not (value.get('completeness') == 'complete' and value.get('consistency') == 'snapshot'):
                raise ObjectQueryError('complete_set_unavailable', 'inputs', 'SetHandle is not a complete snapshot')
            for child in value.values(): walk(child)
        elif isinstance(value, list):
            for child in value: walk(child)
    walk(inputs)
