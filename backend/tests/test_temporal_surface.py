from types import SimpleNamespace
import pytest
from app.schemas.v2.object_query import TemporalSurfaceSpec
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.metadata import OntologyMetadata, TypeMetadata, PropertyMetadata, LinkMetadata
from app.services.v2.temporal_surface import prepare_events


def catalog():
    return OntologyMetadata([
        TypeMetadata('Event', properties={'start_timestamp': PropertyMetadata('start_timestamp', 'datetime'),
                                          'end_timestamp': PropertyMetadata('end_timestamp', 'datetime'),
                                          'event_key': PropertyMetadata('event_key')}),
        TypeMetadata('Machine', properties={'temperature_series_id': PropertyMetadata('temperature_series_id')}),
        TypeMetadata('Sensor', properties={'unit': PropertyMetadata('unit'), 'interpolation': PropertyMetadata('interpolation')}),
    ], [LinkMetadata('EVENT_SUBJECT', 'Event', 'Machine'), LinkMetadata('SENSOR_OF', 'Sensor', 'Machine')])


def spec():
    return TemporalSurfaceSpec(event_type='Event', start_field='start', end_field='end', subject_type='Machine', subject_field='machine', event_link='EVENT_SUBJECT',
                               sensor_type='Sensor', sensor_field='sensor', sensor_link='SENSOR_OF', tsp_property='temperature_series_id', value_field='value', unit='C', interpolation='linear')


def source():
    return SimpleNamespace(objects={('Machine', 'm1'): {}, ('Sensor', 's1'): {'unit': 'C', 'interpolation': 'linear'}},
                           edges=[(('Sensor', 's1'), 'SENSOR_OF', ('Machine', 'm1'))])


def event(start='2026-09-01T00:00:00Z', end='2026-09-01T00:01:00Z'):
    return SimpleNamespace(id='stored-event', event_key='source:e1', status='committed',
        payload={'start': start, 'end': end, 'machine': 'm1', 'sensor': 's1', 'value': 21.5})


def test_event_and_series_are_canonical_and_deterministic():
    nodes, links, syncs = prepare_events('ont', catalog(), source(), [event()], spec())
    assert nodes[0]['entity_type'] == 'Event'
    assert nodes[0]['properties']['start_timestamp'] == '2026-09-01T00:00:00+00:00'
    assert links == [{'source': nodes[0]['id'], 'target': 'm1', 'type': 'EVENT_SUBJECT'}]
    assert syncs[0]['points'][0]['value'] == 21.5
    assert syncs[0]['series_id'] == prepare_events('ont', catalog(), source(), [event()], spec())[2][0]['series_id']


@pytest.mark.parametrize('start,end,code', [
    ('2026-09-01T00:00:00', '2026-09-01T00:01:00Z', 'invalid_event_time'),
    ('2026-09-01T00:02:00Z', '2026-09-01T00:01:00Z', 'invalid_event_time'),
])
def test_event_time_requires_explicit_valid_timezone(start, end, code):
    with pytest.raises(ObjectQueryError) as caught:
        prepare_events('ont', catalog(), source(), [event(start, end)], spec())
    assert caught.value.code == code


def test_subject_and_sensor_must_exist_in_published_graph():
    bad = event(); bad.payload['machine'] = 'unknown'
    with pytest.raises(ObjectQueryError) as caught:
        prepare_events('ont', catalog(), source(), [bad], spec())
    assert caught.value.code == 'invalid_event_subject'
    bad = event(); bad.payload['sensor'] = 'unknown'
    with pytest.raises(ObjectQueryError) as caught:
        prepare_events('ont', catalog(), source(), [bad], spec())
    assert caught.value.code == 'invalid_sensor'


def channel_catalog():
    return OntologyMetadata([TypeMetadata('Machine', properties={
        'volt_series': PropertyMetadata('volt_series'), 'vibration_series': PropertyMetadata('vibration_series'),
        'age': PropertyMetadata('age', 'integer')})], [])


def channel_spec(**kw):
    return TemporalSurfaceSpec(materialize_events=False, start_field='at', subject_type='Machine', subject_field='machine',
                               channels=[{'value_field': 'volt', 'tsp_property': 'volt_series', 'unit': 'V', 'interpolation': 'linear'},
                                         {'value_field': 'vibration', 'tsp_property': 'vibration_series', 'unit': 'mm/s', 'interpolation': 'linear'}], **kw)


def reading(key, at, **values):
    return SimpleNamespace(id=f'stored-{key}', event_key=key, status='committed', payload={'at': at, 'machine': 'm1', **values})


def test_measurement_only_surface_creates_no_event_objects_and_one_series_per_channel():
    events = [reading('r2', '2015-01-01T07:00:00Z', volt=170.0, vibration=40.0),
              reading('r1', '2015-01-01T06:00:00Z', volt=176.2, vibration=45.1),
              reading('r3', '2015-01-01T08:00:00Z', volt=171.0)]  # vibration omitted, not zero
    nodes, links, syncs = prepare_events('ont', channel_catalog(), SimpleNamespace(objects={('Machine', 'm1'): {}}, edges=[]), events, channel_spec())
    assert nodes == [] and links == []
    by_property = {s['property_api_name']: s for s in syncs}
    assert [p['value'] for p in by_property['volt_series']['points']] == [176.2, 170.0, 171.0]
    assert [p['value'] for p in by_property['vibration_series']['points']] == [45.1, 40.0]
    assert by_property['vibration_series']['unit'] == 'mm/s' and by_property['volt_series']['series_id'] != by_property['vibration_series']['series_id']


def test_duplicate_reading_collapses_but_conflicting_reading_fails():
    source = SimpleNamespace(objects={('Machine', 'm1'): {}}, edges=[])
    same = [reading('a', '2015-01-01T06:00:00Z', volt=1.0), reading('b', '2015-01-01T06:00:00+00:00', volt=1.0)]
    assert len(prepare_events('ont', channel_catalog(), source, same, channel_spec())[2][0]['points']) == 1
    clash = [reading('a', '2015-01-01T06:00:00Z', volt=1.0), reading('b', '2015-01-01T06:00:00Z', volt=2.0)]
    with pytest.raises(ObjectQueryError) as caught:
        prepare_events('ont', channel_catalog(), source, clash, channel_spec())
    assert caught.value.code == 'conflicting_measurement'


@pytest.mark.parametrize('kwargs', [
    {'materialize_events': True, 'start_field': 'at', 'subject_type': 'Machine', 'subject_field': 'machine'},  # events need type/end/link
    {'materialize_events': False, 'start_field': 'at', 'subject_type': 'Machine', 'subject_field': 'machine'},  # nothing to project
    {'materialize_events': False, 'start_field': 'at', 'subject_type': 'Machine', 'subject_field': 'machine',
     'channels': [{'value_field': 'a', 'tsp_property': 'x', 'unit': 'V', 'interpolation': 'none'},
                  {'value_field': 'b', 'tsp_property': 'x', 'unit': 'V', 'interpolation': 'none'}]},  # duplicate TSP
])
def test_surface_spec_rejects_ambiguous_mappings(kwargs):
    with pytest.raises(ValueError):
        TemporalSurfaceSpec(**kwargs)


def test_channel_must_target_a_string_tsp_property():
    spec = TemporalSurfaceSpec(materialize_events=False, start_field='at', subject_type='Machine', subject_field='machine',
                               channels=[{'value_field': 'volt', 'tsp_property': 'age', 'unit': 'V', 'interpolation': 'none'}])
    with pytest.raises(ObjectQueryError) as caught:
        prepare_events('ont', channel_catalog(), SimpleNamespace(objects={('Machine', 'm1'): {}}, edges=[]), [], spec)
    assert caught.value.code == 'metadata_mismatch'
