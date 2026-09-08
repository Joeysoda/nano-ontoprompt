from app.tasks.celery_app import celery_app


@celery_app.task(bind=True)
def run_audit(self, task_id: str):
    import app.models  # noqa: F401 — register all tables
    from app.database import SessionLocal
    from app.models.audit_task import AuditTask
    from app.models.model_config import ModelConfig
    # Import every FK target before SQLAlchemy flushes the invocation row.
    # Celery workers do not import FastAPI's main module automatically.
    from app.models.v2.construction import ConstructionRun  # noqa: F401
    from app.models.v2.construction_draft import ConstructionDraft  # noqa: F401
    from app.models.v2.workbench_task import MappingTask, ModelInvocation  # noqa: F401
    from app.models.entity import Entity
    from app.models.entity_instance import EntityInstance
    from app.models.relation import Relation
    from app.models.logic import LogicRule
    from app.models.v2.construction import EvidenceRef
    from app.services.audit_service import run_react_audit
    from app.services.model_config_selector import llm_call_kwargs
    from app.services import encryption_service
    import hashlib
    import json
    import time
    from datetime import datetime, timezone

    db = SessionLocal()
    invocation = None
    invocation_started = time.monotonic()
    try:
        task = db.query(AuditTask).filter(AuditTask.id == task_id).first()
        if not task:
            return

        task.status = "running"
        task.progress = {"stage": "loading ontology", "pct": 10}
        db.commit()

        model_cfg = db.query(ModelConfig).filter(ModelConfig.id == task.model_id).first()
        if not model_cfg:
            task.status = "failed"
            task.error = "Model config not found"
            db.commit()
            return

        call_kwargs = llm_call_kwargs(model_cfg)
        if not call_kwargs:
            task.status = "waiting_for_model"
            task.error = "模型配置无法解密或网关不可用"
            db.commit()
            return
        model_config = {
            "provider": call_kwargs["provider"],
            "api_key": call_kwargs["api_key"],
            "api_base": call_kwargs.get("api_base"),
        }

        # Build a compact, revision-aware ontology snapshot.  Earlier workers
        # looked only at the legacy class tables, so a successful data build
        # could be incorrectly reported as an empty ontology even though its
        # evidence and instance network existed in the workbench store.
        entities_raw = db.query(Entity).filter(Entity.ontology_id == task.ontology_id).all()
        relations_raw = db.query(Relation).filter(Relation.ontology_id == task.ontology_id).all()
        logic_raw = db.query(LogicRule).filter(LogicRule.ontology_id == task.ontology_id).all()

        id_to_name = {e.id: e.name_cn for e in entities_raw}

        instance_rows = db.query(EntityInstance).filter(EntityInstance.ontology_id == task.ontology_id).all()
        instance_counts: dict[str, int] = {}
        instance_samples: dict[str, list[dict]] = {}
        for item in instance_rows:
            instance_counts[item.entity_id] = instance_counts.get(item.entity_id, 0) + 1
            instance_samples.setdefault(item.entity_id, [])
            if len(instance_samples[item.entity_id]) < 3:
                instance_samples[item.entity_id].append({"id": item.id, "row_identity": item.row_identity, "row_data": item.row_data or {}})
        evidence_count = db.query(EvidenceRef).filter(EvidenceRef.ontology_id == task.ontology_id).count()
        # Keep every stable/display identifier that may be referenced by a
        # rule.  Older mapping runs stored compact English names such as
        # ``SensorReadings`` while the visible entity name is
        # ``Sensor Readings``; the audit must not report that formatting
        # difference as a broken ontology reference.
        entities = [
            {
                "id": e.id,
                "name_cn": e.name_cn,
                "name_en": e.name_en,
                "name_abbr": e.name_abbr,
                "canonical_id": e.canonical_id,
                "type": e.type or "Unknown",
                "instance_count": instance_counts.get(e.id, 0),
                "instance_samples": instance_samples.get(e.id, []),
            }
            for e in entities_raw[:300]
        ]
        relations = [
            {
                "id": r.id,
                "source_entity": r.source_entity,
                "target_entity": r.target_entity,
                "source_name": id_to_name.get(r.source_entity, r.source_entity),
                "target_name": id_to_name.get(r.target_entity, r.target_entity),
                "type": r.type,
            }
            for r in relations_raw
        ]
        logic_rules = [
            {"id": r.id, "name_cn": r.name_cn, "linked_entities": r.linked_entities,
             "condition": getattr(r, "condition_json", {}), "effect": getattr(r, "effect_json", {}),
             "evidence": getattr(r, "evidence_json", {})}
            for r in logic_raw
        ]
        snapshot = {
            "entities": entities,
            "relations": relations,
            "logic_rules": logic_rules,
            "instance_count": len(instance_rows),
            "evidence_count": evidence_count,
        }

        task.progress = {"stage": "running react agent", "pct": 30}
        task.react_trace = []
        db.commit()

        request_payload = json.dumps({
            "audit_task_id": task.id,
            "ontology_id": task.ontology_id,
            "revision_id": task.revision_id,
            "model": task.model_name,
            "snapshot": snapshot,
        }, ensure_ascii=False, sort_keys=True, default=str)
        invocation = ModelInvocation(
            audit_task_id=task.id,
            construction_run_id=task.construction_run_id,
            route_alias=str(task.model_name or "qwen3.5:0.8b"),
            provider=str(model_cfg.provider or "local"),
            model_name=str(task.model_name or "qwen3.5:0.8b"),
            status="running",
            phase="audit",
            request_ciphertext=encryption_service.encrypt(request_payload),
            request_hash=hashlib.sha256(request_payload.encode("utf-8")).hexdigest(),
            metadata_json={
                "purpose": "ontology_audit",
                "audit_task_id": task.id,
                "ontology_id": task.ontology_id,
                "revision_id": task.revision_id,
                "hidden_reasoning_logged": False,
            },
        )
        db.add(invocation)
        db.commit()

        max_steps = 12

        def on_step(current: int, total: int):
            db.refresh(task)
            if task.cancel_requested:
                task.status = "cancelled"
                task.error = "用户取消了审查任务"
                db.commit()
                raise RuntimeError("AUDIT_CANCELLED")
            pct = 30 + int(current / total * 55)
            task.progress = {"stage": "running react agent", "pct": pct}
            db.commit()

        def on_trace_step(trace: list):
            # Persist the auditable tool/observation trail, not hidden model
            # reasoning text.  This is the same structured trace shown in the
            # workbench audit page.
            task.react_trace = [
                {key: value for key, value in item.items() if key not in {"thought", "reasoning_content"}}
                for item in trace
            ]
            db.commit()

        findings, trace = run_react_audit(
            ontology_snapshot=snapshot,
            model_config=model_config,
            model_name=task.model_name,
            on_step=on_step,
            on_trace_step=on_trace_step,
            max_steps=max_steps,
        )

        task.progress = {"stage": "saving findings", "pct": 90}
        db.commit()

        task.findings = findings
        task.react_trace = [{key: value for key, value in item.items() if key not in {"thought", "reasoning_content"}} for item in trace]
        has_error = any(isinstance(item, dict) and item.get("error") for item in trace)
        if has_error and str(getattr(model_cfg, "provider", "")).lower() in {"ollama", "local"} and not findings:
            task.status = "waiting_for_model"
            task.error = "本地模型未返回审查结果；请启动 Ollama 并确认 qwen3.5:0.8b 已安装"
        else:
            task.status = "completed"
        task.progress = {"stage": "done", "pct": 100}
        response_payload = json.dumps({"findings": findings, "trace": task.react_trace}, ensure_ascii=False, sort_keys=True, default=str)
        invocation.status = task.status
        invocation.response_ciphertext = encryption_service.encrypt(response_payload)
        invocation.response_hash = hashlib.sha256(response_payload.encode("utf-8")).hexdigest()
        invocation.duration_ms = int((time.monotonic() - invocation_started) * 1000)
        invocation.completed_at = datetime.now(timezone.utc)
        db.commit()

    except Exception as e:
        try:
            db.rollback()
            if str(e) != "AUDIT_CANCELLED":
                task.status = "failed"
                task.error = str(e)
            if invocation is not None:
                stored = db.query(ModelInvocation).filter(ModelInvocation.id == invocation.id).first()
                if stored:
                    stored.status = task.status if task.status in {"cancelled", "failed"} else "failed"
                    stored.error = str(e)[:1000]
                    stored.duration_ms = int((time.monotonic() - invocation_started) * 1000)
                    stored.completed_at = datetime.now(timezone.utc)
            db.commit()
        except Exception:
            pass
    finally:
        db.close()
