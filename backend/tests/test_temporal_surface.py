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
