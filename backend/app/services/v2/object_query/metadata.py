"""Ontology metadata boundary used by Object Query semantic validation."""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session

from app.models.entity import Entity
from app.models.relation import Relation
from app.services.v2.object_query.errors import ObjectQueryError


ORDERED_TYPES = frozenset({"integer", "long", "double", "decimal", "date", "datetime", "timestamp", "string"})
NUMERIC_TYPES = frozenset({"integer", "long", "double", "decimal"})


def normalize_data_type(value: Any) -> str:
    raw = str(value or "string").strip().casefold().replace(" ", "_")
    aliases = {
        "int": "integer", "int32": "integer", "int64": "long", "bigint": "long",
        "float": "double", "number": "double", "numeric": "decimal",
        "bool": "boolean", "str": "string", "text": "string",
        "datetime64": "datetime", "datetime64[ns]": "datetime",
        "list": "array", "object[]": "array",
    }
    return aliases.get(raw, raw)


@dataclass(frozen=True)
class PropertyMetadata:
    api_name: str
    data_type: str = "string"
    nullable: bool = True
    searchable: bool = True
    sortable: bool = True
    aggregatable: bool = True
    is_array: bool = False


@dataclass(frozen=True)
class TypeMetadata:
    api_name: str
    kind: str = "object"
    properties: dict[str, PropertyMetadata] = field(default_factory=dict)


@dataclass(frozen=True)
class LinkMetadata:
    api_name: str
    source_type: str
    target_type: str
    cardinality: str = "many"

    @property
    def is_many(self) -> bool:
        value = self.cardinality.casefold().replace("_", "-")
        return value in {"many", "one-to-many", "many-to-many", "zero-to-many"} or value.endswith("-many")


class OntologyMetadata:
    def __init__(self, types: list[TypeMetadata], links: list[LinkMetadata]):
        self._types: dict[tuple[str, str], TypeMetadata] = {}
        for item in types:
            key = (item.kind, item.api_name)
            if key in self._types:
                raise ValueError(f"Duplicate ontology type api name: {item.kind}:{item.api_name}")
            self._types[key] = item
        self.links = tuple(links)

    def catalog(self) -> dict:
        """Expose the same catalog used by query validation to consumers."""
        from dataclasses import asdict
        return {
            "types": [dict(api_name=item.api_name, kind=item.kind,
                           properties=[asdict(prop) for prop in item.properties.values()])
                      for item in self._types.values()],
            "links": [asdict(link) for link in self.links],
        }

    def resolve_type(self, kind: str, api_name: str, path: str) -> TypeMetadata:
        item = self._types.get((kind, api_name))
        if item is None:
            raise ObjectQueryError(
                "unknown_type", path, f"Unknown {kind} type '{api_name}'",
                type_kind=kind, type_api_name=api_name,
            )
        return item

    def resolve_property(self, type_meta: TypeMetadata, api_name: str, path: str) -> PropertyMetadata:
        item = type_meta.properties.get(api_name)
        if item is None:
            raise ObjectQueryError(
                "unknown_property", path,
                f"Unknown property '{api_name}' on '{type_meta.api_name}'",
                type_api_name=type_meta.api_name, property_api_name=api_name,
            )
        return item

    def resolve_link(self, source_type: str, api_name: str, direction: str, path: str) -> tuple[LinkMetadata, str]:
        matches: list[tuple[LinkMetadata, str]] = []
        for link in self.links:
            if link.api_name != api_name:
                continue
            if direction == "out" and link.source_type == source_type:
                matches.append((link, link.target_type))
            if direction == "in" and link.target_type == source_type:
                matches.append((link, link.source_type))
        if len(matches) == 1:
            return matches[0]
        if len(matches) > 1:
            raise ObjectQueryError(
                "ambiguous_link", path,
                f"Link '{api_name}' is ambiguous in direction '{direction}' from '{source_type}'",
                source_type=source_type, link_api_name=api_name, direction=direction,
                candidate_types=sorted(target for _, target in matches),
            )
        raise ObjectQueryError(
            "invalid_link", path,
            f"Link '{api_name}' is not valid in direction '{direction}' from '{source_type}'",
            source_type=source_type, link_api_name=api_name, direction=direction,
        )


def _type_api_name(entity: Entity) -> str:
    canonical = str(entity.canonical_id or "")
    if canonical.startswith("ontology:") or canonical.startswith("concept:"):
        return canonical.split(":", 1)[1]
    if entity.name_en:
        return str(entity.name_en)
    if entity.type and entity.type != "EntityType":
        return str(entity.type)
    return str(entity.name_cn or entity.id)


def _property_definitions(entity: Entity) -> list[dict[str, Any]]:
    raw = entity.properties or {}
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, dict):
        return []
    definitions = raw.get("property_definitions") or raw.get("properties")
    if isinstance(definitions, list):
        return [item for item in definitions if isinstance(item, dict)]
    reserved = {"schema_version", "source_fields", "evidence", "color", "icon", "data_class", "is_concept", "source"}
    return [
        {"id": str(key), "name": str(key), "type": value if isinstance(value, str) else "string"}
        for key, value in raw.items() if key not in reserved
    ]


def load_sql_metadata(db: Session, ontology_id: str) -> OntologyMetadata:
    """Adapt current and legacy SQL ontology metadata to one query catalog."""
    entities = db.query(Entity).filter(Entity.ontology_id == ontology_id).all()
    by_id: dict[str, str] = {}
    types: list[TypeMetadata] = []
    for entity in entities:
        props = entity.properties if isinstance(entity.properties, dict) else {}
        if props.get("is_instance") or str(entity.canonical_id or "").startswith("instance:"):
            continue
        api_name = _type_api_name(entity)
        by_id[entity.id] = api_name
        properties: dict[str, PropertyMetadata] = {
            "_instance_id": PropertyMetadata("_instance_id", "string", False, True, True, False),
        }
        for raw in _property_definitions(entity):
            prop_name = str(raw.get("id") or raw.get("name") or raw.get("api_name") or raw.get("source_field") or "").strip()
            if not prop_name:
                continue
            data_type = normalize_data_type(raw.get("type") or raw.get("data_type"))
            is_array = data_type == "array" or bool(raw.get("is_array"))
            properties[prop_name] = PropertyMetadata(
                api_name=prop_name,
                data_type=data_type,
                nullable=bool(raw.get("nullable", True)),
                searchable=bool(raw.get("searchable", True)),
                sortable=bool(raw.get("sortable", data_type in ORDERED_TYPES)),
                aggregatable=bool(raw.get("aggregatable", data_type in ORDERED_TYPES or data_type == "boolean")),
                is_array=is_array,
            )
        types.append(TypeMetadata(api_name=api_name, properties=properties))

    # Mapping publishes durable row Entities for relation foreign keys. They
    # reference a type; they are not additional schema definitions.
    for entity in entities:
        props = entity.properties if isinstance(entity.properties, dict) else {}
        if props.get("is_instance") or str(entity.canonical_id or "").startswith("instance:"):
            concept_type = by_id.get(props.get("concept_id"))
            if concept_type:
                by_id[entity.id] = concept_type

    links: list[LinkMetadata] = []
    for relation in db.query(Relation).filter(Relation.ontology_id == ontology_id).all():
        source = by_id.get(relation.source_entity)
        target = by_id.get(relation.target_entity)
        if source is None or target is None:
            continue
        props = relation.properties or {}
        api_name = str(props.get("api_name") or props.get("name") or relation.type or relation.id)
        link = LinkMetadata(
            api_name=api_name,
            source_type=source,
            target_type=target,
            cardinality=str(props.get("cardinality") or "many"),
        )
        if link not in links:
            links.append(link)
    return OntologyMetadata(types, links)
