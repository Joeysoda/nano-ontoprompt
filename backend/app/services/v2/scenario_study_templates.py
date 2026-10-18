"""Small, editable manufacturing Study templates built from pinned Logic Assets."""


def templates() -> list[dict]:
    cycle = {
        "spec_version": "scenario-study/v1",
        "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
        "parameters": [
            {"key": "setup_seconds", "label": "换线准备时间", "type": "decimal", "unit": "s", "default": 30, "minimum": 0},
            {"key": "unit_seconds", "label": "单件加工时间", "type": "decimal", "unit": "s/件", "default": 2, "minimum": 0},
            {"key": "quantity", "label": "批量", "type": "integer", "unit": "件", "default": 10, "minimum": 0},
        ],
        "nodes": [{"key": "cycle", "kind": "function", "implementation": {"key": "cycle_time_v1", "version": "1.0.0"},
                   "inputs": {"setup_seconds": {"parameter": "setup_seconds"}, "unit_seconds": {"parameter": "unit_seconds"}, "quantity": {"parameter": "quantity"}},
                   "outputs": {"minutes": "total_minutes"}}],
        "edges": [], "metrics": [{"key": "cycle_minutes", "label": "批次周期", "type": "decimal", "direction": "minimize", "unit": "min", "source": "cycle.minutes"}],
        "constraints": [], "budgets": {},
    }
    supplier = {
        "spec_version": "scenario-study/v1",
        "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
        "parameters": [
            {"key": "demands", "label": "生产订单需求", "type": "json", "default": [{"id": "WO-100", "item": "pump", "quantity": 12}]},
            {"key": "supply_options", "label": "供应商供给", "type": "json", "default": [{"supplier": "Supplier-A", "item": "steel_coil", "quantity": 80, "lead_time": 4}, {"supplier": "Supplier-B", "item": "steel_coil", "quantity": 60, "lead_time": 7}]},
            {"key": "boms", "label": "多级 BOM 用量", "type": "json", "default": [{"parent_item": "pump", "component_item": "subassembly", "quantity_per": 2}, {"parent_item": "subassembly", "component_item": "steel_coil", "quantity_per": 5}]},
        ],
        "nodes": [
            {"key": "impact", "kind": "function", "implementation": {"key": "supplier_disruption_impact_v1", "version": "1.0.0"},
             "inputs": {"demands": {"parameter": "demands"}, "supply_options": {"parameter": "supply_options"}, "boms": {"parameter": "boms"}},
             "outputs": {"shortages": "shortages", "feasible": "feasible"}},
            {"key": "shortage_count", "kind": "aggregate_metric", "inputs": {"shortages": {"from": "impact.shortages"}},
             "outputs": {"count": "number"}, "config": {"operation": "count"}},
        ],
        "edges": [{"source": "impact.shortages", "target": "shortage_count.shortages"}],
        "metrics": [{"key": "shortage_count", "label": "缺料订单数", "type": "integer", "direction": "minimize", "unit": "orders", "source": "shortage_count.count"}],
        "constraints": [{"key": "supply_feasible", "expression_ref": "impact.feasible", "severity": "hard", "config": {"operator": "eq", "value": True}}],
        "budgets": {},
    }
    supply_chain = {
        "spec_version": "scenario-study/v1",
        "context_policy": {"scenario_mode": "pinned_on_run", "time_required": True},
        "parameters": [
            {"key": "demands", "label": "订单需求", "type": "json", "default": [
                {"id": "O1", "item": "P1", "quantity": 5, "due_at": 2, "resource": "line-1", "processing_days": 1, "priority": 2},
                {"id": "O2", "item": "P2", "quantity": 10, "due_at": 3, "resource": "line-1", "processing_days": 1, "priority": 1},
            ]},
            {"key": "supply_options", "label": "供应来源与假设", "type": "json", "default": [
                {"supplier": "Supplier-A", "item": "M1", "quantity": 30, "lead_time": 1, "unit_cost": 10, "qualified": True},
                {"supplier": "Supplier-B", "item": "M1", "quantity": 20, "lead_time": 1, "unit_cost": 13, "qualified": True},
                {"supplier": "Supplier-C", "item": "M1", "quantity": 30, "lead_time": 1, "unit_cost": 16, "qualified": True},
                {"supplier": "Supplier-D", "item": "M1", "quantity": 100, "lead_time": 1, "unit_cost": 8, "qualified": False},
                {"supplier": "Supplier-A", "item": "M2", "quantity": 20, "lead_time": 1, "unit_cost": 9, "qualified": True},
            ]},
            {"key": "boms", "label": "多级 BOM", "type": "json", "default": [
                {"parent_item": "P1", "component_item": "SUB", "quantity_per": 2},
                {"parent_item": "P2", "component_item": "SUB", "quantity_per": 1},
                {"parent_item": "P2", "component_item": "M2", "quantity_per": 2},
                {"parent_item": "SUB", "component_item": "M1", "quantity_per": 3},
            ]},
            {"key": "inventory", "label": "期初库存", "type": "json", "default": [{"item": "M1", "quantity": 30}, {"item": "M2", "quantity": 20}]},
            {"key": "scenario_time", "label": "情景开始时间", "type": "datetime", "default": "2026-01-01T00:00:00Z"},
        ],
        "nodes": [{"key": "chain", "kind": "function", "implementation": {"key": "supply_chain_reasoning_v1", "version": "1.0.0"},
                   "inputs": {"demands": {"parameter": "demands"}, "supply_options": {"parameter": "supply_options"}, "boms": {"parameter": "boms"}, "inventory": {"parameter": "inventory"}, "scenario_time": {"parameter": "scenario_time"}},
                   "outputs": {"feasible": "feasible", "total_cost": "total_cost", "late_order_count": "late_order_count", "shortages": "shortages", "demand_impacts": "demand_impacts", "schedule": "schedule", "alternatives": "alternatives"}}],
        "edges": [],
        "metrics": [
            {"key": "total_cost", "label": "采购成本", "type": "decimal", "direction": "minimize", "unit": "cost", "source": "chain.total_cost"},
            {"key": "late_order_count", "label": "迟交订单数", "type": "integer", "direction": "minimize", "unit": "orders", "source": "chain.late_order_count"},
        ],
        "constraints": [{"key": "chain_feasible", "expression_ref": "chain.feasible", "severity": "hard", "config": {"operator": "eq", "value": True}}],
        "budgets": {"max_nodes": 50, "max_objects": 100000, "max_wall_seconds": 900, "max_artifact_bytes": 52428800},
    }
    capacity = {
        "spec_version": "scenario-study/v1",
        "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
        "parameters": [
            {"key": "tasks", "label": "生产工序", "type": "json", "default": [{"id": "OP-10", "duration": 45, "due_at": 50, "due_date": "2026-11-01", "resource": "line-1"}, {"id": "OP-20", "duration": 75, "due_at": 110, "due_date": "2026-11-02", "resource": "line-1"}, {"id": "OP-30", "duration": 60, "due_at": 65, "due_date": "2026-11-03", "resource": "line-2"}]},
            {"key": "capacity", "label": "可用产能", "type": "decimal", "unit": "min", "default": 240, "minimum": 0},
        ],
        "nodes": [
            {"key": "schedule", "kind": "function", "implementation": {"key": "capacity_replan_v1", "version": "1.0.0"},
             "inputs": {"tasks": {"parameter": "tasks"}, "capacity": {"parameter": "capacity"}},
             "outputs": {"makespan": "makespan", "feasible": "feasible", "late_tasks": "late_tasks"}},
            {"key": "late_count", "kind": "aggregate_metric", "inputs": {"late_tasks": {"from": "schedule.late_tasks"}},
             "outputs": {"count": "number"}, "config": {"operation": "count"}},
        ],
        "edges": [{"source": "schedule.late_tasks", "target": "late_count.late_tasks"}],
        "metrics": [{"key": "late_orders", "label": "逾期工序数", "type": "integer", "direction": "minimize", "unit": "operations", "source": "late_count.count"},
                    {"key": "makespan", "label": "计划工时", "type": "decimal", "direction": "minimize", "unit": "min", "source": "schedule.makespan"}],
        "constraints": [{"key": "capacity_feasible", "expression_ref": "schedule.feasible", "severity": "hard", "config": {"operator": "eq", "value": True}}],
        "budgets": {},
    }
    energy = {
        "spec_version": "scenario-study/v1",
        "context_policy": {"scenario_mode": "pinned_on_run", "time_required": False},
        "parameters": [
            {"key": "rates_per_kwh", "label": "示例电价（每 kWh）", "type": "json", "default": {"Light_Load": 0.10, "Medium_Load": 0.15, "Maximum_Load": 0.22}},
            {"key": "maximum_load_rate_change", "label": "高峰电价变化比例", "type": "decimal", "unit": "%", "default": 0.25, "minimum": -1, "maximum": 10},
        ],
        "nodes": [{"key": "energy", "kind": "function", "implementation": {"key": "uci_steel_energy_tariff_v1", "version": "1.0.0"},
                   "inputs": {"rates_per_kwh": {"parameter": "rates_per_kwh"}, "maximum_load_rate_change": {"parameter": "maximum_load_rate_change"}},
                   "outputs": {"cost_delta": "cost_delta", "usage_delta": "usage_delta_kwh", "dataset": "dataset_sha256"}}],
        "edges": [],
        "metrics": [{"key": "energy_cost_delta", "label": "电费变化", "type": "decimal", "direction": "minimize", "unit": "tariff units", "source": "energy.cost_delta"}],
        "constraints": [{"key": "physical_energy_unchanged", "expression_ref": "energy.usage_delta", "severity": "hard", "config": {"operator": "eq", "value": 0}}],
        "budgets": {},
    }
    return [
        {"template_id": "supply_chain_reasoning", "name": "供应停供到订单交付推理", "description": "多级 BOM、合格替代来源、库存、到货时间、生产资源和订单交付的端到端情景推理。示例数据为合成数据，结果不声称全局最优。", "spec": supply_chain},
        {"template_id": "supplier_disruption", "name": "供应中断与缺料影响", "description": "改变供应商供给、停供比例或订单需求，计算剩余缺口。参数样例为合成制造数据。", "spec": supplier},
        {"template_id": "capacity_replan", "name": "产能与设备变化", "description": "调整工序时长和可用产能，查看计划工时、逾期工序和可行状态。参数样例为合成制造数据。", "spec": capacity},
        {"template_id": "steel_energy_tariff", "name": "钢铁能耗与电价", "description": "基于固定 UCI 钢铁能耗全年汇总，改变高峰电价并验证物理能耗不变；电价为示例假设。", "spec": energy},
        {"template_id": "cycle_time", "name": "工艺节拍变化", "description": "调整换线时间、单件加工时间和批量，比较批次周期。", "spec": cycle},
    ]
