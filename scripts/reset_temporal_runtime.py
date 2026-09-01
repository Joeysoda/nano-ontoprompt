#!/usr/bin/env python3
"""Safely remove legacy temporal demo runtime data.

Only objects explicitly marked as temporal/ICEWS/BTS are selected.  The
default is a dry run; ``--apply`` requires a second explicit confirmation and
creates a JSON inventory before deleting database rows and FalkorDB graphs.
"""
from __future__ import annotations
import argparse, json, os, sys
from datetime import datetime, timezone

def main() -> int:
    sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
    parser = argparse.ArgumentParser(); parser.add_argument('--dry-run', action='store_true'); parser.add_argument('--apply', action='store_true'); parser.add_argument('--yes', action='store_true'); args = parser.parse_args()
    if not args.dry_run and not args.apply: args.dry_run = True
    from app.models.v2.connection import Connection  # noqa: F401 ensure FK metadata
    from app.database import SessionLocal
    from app.models.ontology import OntologyProject
    from app.models.v2.construction import ConstructionRun, EvidenceRef
    from app.models.v2.dataset import Dataset, DatasetVersion
    from app.services.v2.graph.falkordb_service import FalkorDBService, graph_name_for_ontology
    db = SessionLocal()
    try:
        all_runs = db.query(ConstructionRun).filter(ConstructionRun.mode.in_(['temporal','temporal_pipeline'])).all()
        # Keep the newly validated FactoryNet run(s). Remove legacy ICEWS and
        # malformed non-FactoryNet temporal runs only.
        runs = [r for r in all_runs if (r.config or {}).get('source_id') != 'factorynet_cnc' and (r.config or {}).get('source') != 'factorynet_cnc' and (r.config or {}).get('adapter') != 'factorynet']
        run_ontology_ids = {r.ontology_id for r in runs if r.ontology_id}
        # Remove an ontology only when it is itself a temporal pipeline.  A
        # malformed generic run may live on the regular C-MAPSS ontology; in
        # that case delete the run but preserve the ontology and its graph.
        temporal_ontology_ids = {o.id for o in db.query(OntologyProject).filter(OntologyProject.id.in_(run_ontology_ids)).all() if o.build_mode == 'temporal_pipeline'} if run_ontology_ids else set()
        ontology_ids = temporal_ontology_ids
        ontologies = db.query(OntologyProject).filter(OntologyProject.id.in_(ontology_ids)).all() if ontology_ids else []
        datasets = [d for d in db.query(Dataset).all() if (d.schema_json or {}).get('source_id') == 'icews_2023_demo']
        evidence_count = db.query(EvidenceRef).filter(EvidenceRef.ontology_id.in_(ontology_ids)).count() if ontology_ids else 0
        inventory = {'generated_at': datetime.now(timezone.utc).isoformat(), 'runs': [r.id for r in runs], 'ontologies': [{'id':o.id,'name':o.name} for o in ontologies], 'datasets': [d.id for d in datasets], 'evidence_refs': evidence_count, 'graphs': [graph_name_for_ontology(i) for i in ontology_ids]}
        print(json.dumps(inventory, ensure_ascii=False, indent=2))
        if args.dry_run: return 0
        if not args.yes:
            raise SystemExit('即将删除以上 temporal 运行数据；请添加 --yes 确认')
        out = os.path.join(os.getenv('XDG_CACHE_HOME','/tmp'), 'nano-temporal-backups'); os.makedirs(out, exist_ok=True)
        path = os.path.join(out, f'reset-{datetime.now().strftime("%Y%m%d-%H%M%S")}.json'); open(path,'w').write(json.dumps(inventory,ensure_ascii=False,indent=2)); print('备份清单:',path)
        for r in runs: db.delete(r)
        for oid in ontology_ids:
            db.query(EvidenceRef).filter(EvidenceRef.ontology_id == oid).delete(synchronize_session=False)
        # Remove only legacy stable BTS schema rows accidentally attached to
        # C-MAPSS; regular datasets, pipelines and model configs stay intact.
        # Remove only the six deterministic BTS schema rows accidentally
        # attached to the preserved C-MAPSS ontology.
        from app.models.entity import Entity
        from app.models.relation import Relation
        legacy_prefix = 'schema:b072efac-5905-4933-b912-b7dea6a40627:'
        legacy_ids = [e.id for e in db.query(Entity).filter(Entity.id.like(legacy_prefix + '%')).all()]
        if legacy_ids:
            db.query(Relation).filter((Relation.id.like(legacy_prefix + '%')) | Relation.source_entity.in_(legacy_ids) | Relation.target_entity.in_(legacy_ids)).delete(synchronize_session=False)
            db.query(Entity).filter(Entity.id.in_(legacy_ids)).delete(synchronize_session=False)
        db.commit()
        for o in ontologies:
            db.delete(o)
        db.commit()
        for d in datasets:
            for v in db.query(DatasetVersion).filter(DatasetVersion.dataset_id == d.id).all(): db.delete(v)
            db.delete(d)
        db.commit()
        service = FalkorDBService()
        for oid in ontology_ids:
            try: service.delete_graph(oid)
            except Exception as exc: print('graph delete skipped', oid, exc)
        print('已删除 temporal 运行对象。')
        return 0
    finally: db.close()
if __name__ == '__main__': raise SystemExit(main())
