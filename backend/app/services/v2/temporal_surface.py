"""Explicit adapter from ingestion events to canonical Event and TSP surfaces."""
from collections import defaultdict
from datetime import datetime, timezone
from hashlib import sha256
from math import isfinite
from app.services.v2.object_query.errors import ObjectQueryError
from app.services.v2.object_query.normalize import stable_hash


def timestamp(value, path):
    if not isinstance(value, str):
        raise ObjectQueryError('invalid_event_time', path, 'Event timestamps must be ISO 8601 strings with a timezone')
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    except ValueError as exc:
        raise ObjectQueryError('invalid_event_time', path, 'Event timestamp is invalid') from exc
    if parsed.tzinfo is None:
        raise ObjectQueryError('invalid_event_time', path, 'Event timestamp requires a timezone')
    return parsed.astimezone(timezone.utc).isoformat()


def prepare_events(ontology_id, metadata, source, events, spec):
    event_type = metadata.resolve_type('object', spec.event_type, 'temporal_surface.event_type')
    root_type = metadata.resolve_type('object', spec.subject_type, 'temporal_surface.subject_type')
    for prop in ('start_timestamp', 'end_timestamp', 'event_key'):
        if prop not in event_type.properties:
            raise ObjectQueryError('metadata_mismatch', 'temporal_surface.event_type', f'Event type requires {prop}')
    for prop in ('start_timestamp', 'end_timestamp'):
        if event_type.properties[prop].data_type not in {'datetime', 'timestamp'}:
            raise ObjectQueryError('metadata_mismatch', 'temporal_surface.event_type', f'{prop} must be a timestamp')
    _, event_target = metadata.resolve_link(spec.event_type, spec.event_link, 'out', 'temporal_surface.event_link')
    if event_target != spec.subject_type:
        raise ObjectQueryError('metadata_mismatch', 'temporal_surface.event_link', 'Event link must target the declared subject type')
    if spec.sensor_type:
        sensor_type = metadata.resolve_type('object', spec.sensor_type, 'temporal_surface.sensor_type')
        _, sensor_target = metadata.resolve_link(spec.sensor_type, spec.sensor_link, 'out', 'temporal_surface.sensor_link')
        if sensor_target != spec.subject_type:
            raise ObjectQueryError('metadata_mismatch', 'temporal_surface.sensor_link', 'Sensor link must target the declared subject type')
        if spec.tsp_property not in root_type.properties or root_type.properties[spec.tsp_property].data_type != 'string':
            raise ObjectQueryError('metadata_mismatch', 'temporal_surface.tsp_property', 'Root TSP property must be declared as a string series ID')
        for prop in ('unit', 'interpolation'):
            if prop not in sensor_type.properties:
                raise ObjectQueryError('metadata_mismatch', 'temporal_surface.sensor_type', f'Sensor type requires {prop}')
    nodes, links, grouped = [], [], defaultdict(list)
    seen = set()
    for event in events:
        if event.status != 'committed':
            continue
        payload = event.payload or {}
        start = timestamp(payload.get(spec.start_field), f'event.{event.event_key}.{spec.start_field}')
        end = timestamp(payload.get(spec.end_field), f'event.{event.event_key}.{spec.end_field}')
        if end < start:
            raise ObjectQueryError('invalid_event_time', 'temporal_surface', 'Event ends before it starts')
        subject = payload.get(spec.subject_field)
        if not isinstance(subject, str) or (spec.subject_type, subject) not in source.objects:
            raise ObjectQueryError('invalid_event_subject', 'temporal_surface.subject_field', 'Event subject is absent from the published snapshot')
        key = f'event:{sha256(f"{ontology_id}:{event.event_key}".encode()).hexdigest()[:40]}'
        if key in seen or (spec.event_type, key) in source.objects:
            raise ObjectQueryError('duplicate_event', 'temporal_surface', 'Event Object identity is ambiguous')
        seen.add(key)
        nodes.append({'id': key, 'entity_type': spec.event_type, 'properties': {'start_timestamp': start, 'end_timestamp': end, 'event_key': event.event_key, 'source_event_id': event.id}})
        links.append({'source': key, 'target': subject, 'type': spec.event_link})
        if spec.sensor_type:
            sensor = payload.get(spec.sensor_field)
            if not isinstance(sensor, str) or (spec.sensor_type, sensor) not in source.objects:
                raise ObjectQueryError('invalid_sensor', 'temporal_surface.sensor_field', 'Sensor is absent from the published snapshot')
            if ((spec.sensor_type, sensor), spec.sensor_link, (spec.subject_type, subject)) not in source.edges:
                raise ObjectQueryError('invalid_sensor_link', 'temporal_surface.sensor_link', 'Sensor is not linked to the event subject')
            sensor_properties = source.objects[(spec.sensor_type, sensor)]
            if sensor_properties.get('unit') != spec.unit or sensor_properties.get('interpolation') != spec.interpolation:
                raise ObjectQueryError('metadata_mismatch', 'temporal_surface.sensor_type', 'Sensor unit or interpolation differs from the declared mapping')
            value = payload.get(spec.value_field)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not isfinite(value):
                raise ObjectQueryError('invalid_measurement', 'temporal_surface.value_field', 'Time series value must be a finite number')
            series_id = f'series:{sha256(f"{ontology_id}:{spec.subject_type}:{subject}:{spec.sensor_type}:{sensor}:{spec.tsp_property}".encode()).hexdigest()[:40]}'
            grouped[(spec.subject_type, subject, spec.sensor_type, sensor, series_id)].append({'timestamp': start, 'value': value, 'source_event_id': event.id})
    syncs = []
    for (root_type, root_id, sensor_type, sensor_id, series_id), points in grouped.items():
        points.sort(key=lambda item: (item['timestamp'], item['source_event_id']))
        if len(points) > 10000:
            raise ObjectQueryError('query_too_complex', 'temporal_surface', 'Time series exceeds 10,000 points')
        syncs.append({'root_type': root_type, 'root_id': root_id, 'sensor_type': sensor_type, 'sensor_id': sensor_id,
                      'series_id': series_id, 'property_api_name': spec.tsp_property, 'unit': spec.unit,
                      'interpolation': spec.interpolation, 'points': points})
    return nodes, links, syncs


def install_surface(db, graph_service, view, nodes, links, syncs):
    from app.models.v2.time_series import TimeSeriesSync
    if nodes:
        graph_service.upsert_instances(view.graph_key, nodes)
        graph_service.upsert_relations(view.graph_key, links)
    for sync in syncs:
        root = graph_service._graph(view.graph_key)
        root.query('MATCH (n:Instance {_instance_id:$id, _type:$type, _ontology_id:$ontology}) SET n += $patch',
                   params={'id': sync['root_id'], 'type': sync['root_type'], 'ontology': view.graph_key,
                           'patch': {sync['property_api_name']: sync['series_id']}})
        db.add(TimeSeriesSync(ontology_id=view.ontology_id, data_view_id=view.id, series_id=sync['series_id'],
                              root_type=sync['root_type'], root_id=sync['root_id'], sensor_type=sync['sensor_type'],
                              sensor_id=sync['sensor_id'], property_api_name=sync['property_api_name'], unit=sync['unit'],
                              interpolation=sync['interpolation'], points=sync['points'], point_count=len(sync['points'])))
    view.object_count += len(nodes)
    view.edge_count += len(links)
    view.content_digest = stable_hash({'source': view.content_digest, 'events': nodes, 'links': links, 'series': syncs})
    db.commit()
