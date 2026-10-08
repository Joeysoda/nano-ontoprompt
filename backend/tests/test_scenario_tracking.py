from types import SimpleNamespace
from app.services.v2.scenario_tracking import rebase_edits


def snapshot(objects, edges=()):
    return SimpleNamespace(objects=objects, edges=edges)


def test_rebase_preserves_overlay_and_accepts_unrelated_live_changes():
    key = ('Order', 'one')
    edits, conflicts = rebase_edits('ont', snapshot({key: {'status': 'open', 'amount': 1}}),
                                   snapshot({key: {'status': 'closed', 'amount': 1}}),
                                   snapshot({key: {'status': 'open', 'amount': 2}}))
    assert not conflicts
    assert [(edit.op, edit.property, edit.value, edit.expected_old_value) for edit in edits] == [('set_property', 'status', 'closed', 'open')]


def test_rebase_reports_overlapping_change_without_losing_user_delta():
    key = ('Order', 'one')
    edits, conflicts = rebase_edits('ont', snapshot({key: {'status': 'open'}}),
                                   snapshot({key: {'status': 'closed'}}), snapshot({key: {'status': 'cancelled'}}))
    assert edits == []
    assert conflicts[0]['reason'] == 'property_changed'


def test_rebase_detects_missing_vs_null_and_deleted_object_new_links():
    key, other = ('Order', 'one'), ('Order', 'two')
    _, conflicts = rebase_edits('ont', snapshot({key: {}}), snapshot({key: {'status': 'closed'}}), snapshot({key: {'status': None}}))
    assert conflicts[0]['reason'] == 'property_changed'
    _, conflicts = rebase_edits('ont', snapshot({key: {}, other: {}}), snapshot({other: {}}), snapshot({key: {}, other: {}}, [(key, 'LINK', other)]))
    assert conflicts[0]['reason'] == 'deleted_object_changed'


def test_rebase_already_applied_delta_is_idempotent():
    key = ('Order', 'one')
    edits, conflicts = rebase_edits('ont', snapshot({key: {'status': 'open'}}), snapshot({key: {'status': 'closed'}}), snapshot({key: {'status': 'closed'}}))
    assert edits == []
    assert conflicts == []
