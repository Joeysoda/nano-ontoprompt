/* eslint-disable @typescript-eslint/no-explicit-any */
import { useEffect, useMemo, useState } from "react";
import { AlertTriangle, CheckCircle2, Loader2, Play, RotateCcw } from "lucide-react";
import { useNavigate, useSearchParams } from "react-router-dom";
import { apiClientV2 } from "@/api/client";

type Node = { id: string; entity_type?: string; event_seq?: string | number | null; properties?: Record<string, any> };
type Context = { base_revision_id?: string; target_instance_id?: string; episode_id?: string; at?: string; mode?: string; nodes: Node[]; edges: Array<{ id?: string; source: string; target: string; type: string }>; rules?: any[]; limits?: Record<string, number> };
type Run = { id: string; status: string; stage: string; progress: number; error?: string; diff?: { added?: string[]; removed?: string[]; unchanged?: string[]; proofs?: any[] }; engine?: string; engine_version?: string };
type RelationMode = "none" | "add" | "remove";

const stageNames: Record<string, string> = { prepare_baseline: "准备基线", baseline_reasoning: "基线推理", apply_assumption: "应用假设", scenario_reasoning: "情景推理", generate_diff: "生成差异", completed: "完成" };

type DiffStatus = "added" | "removed" | "unchanged";

function factForEdge(edge: { source: string; target: string; type: string }) {
  return `${String(edge.type || "RELATED").replace(/[^A-Za-z0-9_:-]/g, "_").toUpperCase()}(${edge.source}, ${edge.target})`;
}

function factParts(fact: string): { predicate: string; source: string; target: string } | null {
  const match = /^([A-Z][A-Z0-9_]*)\\((.*), (.*)\\)$/.exec(fact);
  if (!match) return null;
  return { predicate: match[1], source: match[2], target: match[3] };
}

function nodeText(node: Node) {
  const props = node.properties || {};
  return String(props.name || props.label || props.equipment_id || props.episode_id || props.value || node.id).slice(0, 24);
}

function ScenarioGraph({ context, diff }: { context: Context; diff: NonNullable<Run["diff"]> }) {
  const added = new Set(diff.added || []);
  const removed = new Set(diff.removed || []);
  const edgeRows = new Map<string, { source: string; target: string; predicate: string; status: DiffStatus }>();
  (context.edges || []).forEach((edge) => {
    const predicate = String(edge.type || "RELATED").replace(/[^A-Za-z0-9_:-]/g, "_").toUpperCase();
    const row = { source: edge.source, target: edge.target, predicate, status: (added.has(factForEdge(edge)) ? "added" : removed.has(factForEdge(edge)) ? "removed" : "unchanged") as DiffStatus };
    edgeRows.set(`${row.predicate}|${row.source}|${row.target}`, row);
  });
  // A newly inferred relation has no source edge in the baseline context. Add
  // it to the same bounded canvas so the green scenario result is visible.
  [...added, ...removed].forEach((fact) => {
    const parts = factParts(fact);
    // Property/type support facts remain in the textual diff; the canvas is
    // reserved for actual binary relationships between instance nodes.
    if (!parts || parts.predicate.startsWith("PROP_") || parts.predicate.startsWith("TYPE_") || (!parts.source && !parts.target)) return;
    const key = `${parts.predicate}|${parts.source}|${parts.target}`;
    if (!edgeRows.has(key)) edgeRows.set(key, { ...parts, status: added.has(fact) ? "added" : "removed" });
  });
  const candidateIds = new Set<string>();
  edgeRows.forEach((edge) => { candidateIds.add(edge.source); candidateIds.add(edge.target); });
  const sourceNodes = [...(context.nodes || [])].filter((node) => candidateIds.has(node.id));
  const known = new Set(sourceNodes.map((node) => node.id));
  candidateIds.forEach((id) => {
    if (!known.has(id)) sourceNodes.push({ id, entity_type: id.startsWith("scenario:") ? "ToolCondition" : "实体", properties: { value: id.startsWith("scenario:") ? id.split(":").slice(-1)[0] : id } });
  });
  const nodes = sourceNodes.slice(0, 32);
  const nodeIds = new Set(nodes.map((node) => node.id));
  const edges = [...edgeRows.values()].filter((edge) => nodeIds.has(edge.source) && nodeIds.has(edge.target)).slice(0, 56);
  const positions = new Map(nodes.map((node, index) => {
    const angle = (Math.PI * 2 * index) / Math.max(nodes.length, 1) - Math.PI / 2;
    return [node.id, { x: 390 + Math.cos(angle) * 275, y: 190 + Math.sin(angle) * 140 }];
  }));
  const color: Record<DiffStatus, string> = { added: "#059669", removed: "#dc2626", unchanged: "#94a3b8" };
  return <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-3" data-testid="what-if-graph">
    <div className="mb-2 flex flex-wrap items-center justify-between gap-2">
      <span className="text-xs font-medium text-slate-700">情景数据模型</span>
      <div className="flex flex-wrap items-center gap-3 text-[11px] text-slate-500"><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-emerald-600" />新增</span><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-red-600" />消失</span><span><i className="mr-1 inline-block h-2 w-2 rounded-full bg-slate-400" />未变化</span></div>
    </div>
    <div className="overflow-x-auto rounded-md border border-slate-200 bg-white">
      <svg viewBox="0 0 780 380" className="h-[300px] min-w-[680px] w-full" role="img" aria-label="What-If 情景数据模型关系差异">
        <defs><marker id="what-if-arrow" markerWidth="8" markerHeight="8" refX="7" refY="3" orient="auto"><path d="M0,0 L0,6 L7,3 z" fill="#64748b" /></marker></defs>
        {edges.map((edge) => {
          const start = positions.get(edge.source); const end = positions.get(edge.target);
          if (!start || !end) return null;
          const stroke = color[edge.status];
          return <g key={`${edge.predicate}-${edge.source}-${edge.target}`}><line x1={start.x} y1={start.y} x2={end.x} y2={end.y} stroke={stroke} strokeWidth={edge.status === "unchanged" ? 1.2 : 2.4} strokeDasharray={edge.status === "removed" ? "5 4" : undefined} markerEnd="url(#what-if-arrow)" opacity={edge.status === "unchanged" ? 0.65 : 0.95} /><text x={(start.x + end.x) / 2} y={(start.y + end.y) / 2 - 4} textAnchor="middle" className="fill-slate-500 text-[9px]">{edge.predicate.slice(0, 17)}</text></g>;
        })}
        {nodes.map((node) => { const point = positions.get(node.id); if (!point) return null; const isTarget = node.id === context.target_instance_id; return <g key={node.id}><circle cx={point.x} cy={point.y} r={isTarget ? 24 : 20} fill={isTarget ? "#1e293b" : "#0f766e"} stroke={isTarget ? "#f59e0b" : "#ffffff"} strokeWidth={isTarget ? 3 : 2} /><text x={point.x} y={point.y + 3} textAnchor="middle" className="fill-white text-[9px] font-medium">{nodeText(node).slice(0, 13)}</text><text x={point.x} y={point.y + 34} textAnchor="middle" className="fill-slate-500 text-[9px]">{String(node.entity_type || "实体").slice(0, 15)}</text></g>; })}
      </svg>
    </div>
    {!edges.length && <p className="mt-2 text-[11px] text-slate-400">当前范围没有可绘制的关系</p>}
  </div>;
}

export default function WhatIfTab({ ontologyId }: { ontologyId: string }) {
  const [params] = useSearchParams();
  const navigate = useNavigate();
  const [context, setContext] = useState<Context | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [step, setStep] = useState(1);
  const [scenarioId, setScenarioId] = useState("");
  const [run, setRun] = useState<Run | null>(null);
  const [scenarioValue, setScenarioValue] = useState("worn");
  const [relationMode, setRelationMode] = useState<RelationMode>("none");
  const [relationSource, setRelationSource] = useState("");
  const [relationTarget, setRelationTarget] = useState("");
  const [relationType, setRelationType] = useState("RELATED");
  const [relationToRemove, setRelationToRemove] = useState("");
  const [disabledRuleIds, setDisabledRuleIds] = useState<string[]>([]);
  const [temporaryRuleEnabled, setTemporaryRuleEnabled] = useState(true);
  const targetId = params.get("target_instance_id") || params.get("target") || "";
  useEffect(() => {
    let cancelled = false;
    if (!targetId) {
      return () => { cancelled = true; };
    }
    // The fetch lifecycle owns these two visible state flags.
    setLoading(true); setError(""); // eslint-disable-line react-hooks/set-state-in-effect
    apiClientV2.get<Context>(`/ontologies/${ontologyId}/what-if/context`, { params: { target_instance_id: targetId || undefined, episode_id: params.get("episode_id") || undefined, at: params.get("at") || undefined, mode: params.get("mode") || "cumulative" } }).then((result) => { if (!cancelled) { setContext(result); setStep(1); } }).catch((reason: any) => { if (!cancelled) setError(String(reason?.detail?.message || reason?.message || "无法读取推演基线")); }).finally(() => { if (!cancelled) setLoading(false); });
    return () => { cancelled = true; };
  }, [ontologyId, targetId, params]);
  useEffect(() => {
    if (!run || !scenarioId || !["queued", "running"].includes(run.status)) return;
    const timer = window.setInterval(() => { void apiClientV2.get<Run>(`/ontologies/${ontologyId}/what-if/runs/${run.id}`).then(setRun).catch(() => undefined); }, 900);
    return () => window.clearInterval(timer);
  }, [ontologyId, run, scenarioId]);
  const target = useMemo(() => context?.nodes.find((node) => node.id === (context.target_instance_id || targetId)) || context?.nodes[0], [context, targetId]);
  const currentValue = target?.properties?.ctx_tool_condition || target?.properties?.tool_condition || "unworn";
  const relationOptions = useMemo(() => (context?.edges || []).filter((edge) => edge.source && edge.target).slice(0, 80), [context]);
  const temporaryRule = {
    id: "factorynet-phase-tool-state",
    conditions: [
      { kind: "relationship", predicate: "IN_PHASE", source: "?observation", target: "?phase" },
      { kind: "relationship", predicate: "HAS_TOOL_CONDITION", source: "?observation", target: "?condition" },
    ],
    effect: { kind: "relationship", predicate: "PHASE_TOOL_STATE", source: "?phase", target: "?condition" },
  };
  const runScenario = async () => {
    if (!context || !target) return;
    setError("");
    try {
      const assumptions: Array<Record<string, string>> = [{ kind: "set_property", instance_id: target.id, property: "ctx_tool_condition", value: scenarioValue }];
      if (relationMode === "add") {
        if (!relationSource || !relationTarget || !relationType.trim()) {
          setError("请补充要新增的关系起点、终点和名称");
          return;
        }
        assumptions.push({ kind: "add_relation", source: relationSource, target: relationTarget, type: relationType.trim() });
      }
      if (relationMode === "remove") {
        const selectedRelation = relationOptions.find((edge) => `${edge.source}|${edge.type || "RELATED"}|${edge.target}` === relationToRemove);
        if (!selectedRelation) {
          setError("请选择要移除的现有关系");
          return;
        }
        assumptions.push({ kind: "remove_relation", source: selectedRelation.source, target: selectedRelation.target, type: selectedRelation.type || "RELATED" });
      }
      const scenario = await apiClientV2.post<any>(`/ontologies/${ontologyId}/what-if/scenarios`, { name: "FactoryNet 刀具状态情景", baseline: context, assumptions, rule_overrides: { disabled: disabledRuleIds, temporary: temporaryRuleEnabled ? [temporaryRule] : [] } });
      setScenarioId(scenario.id);
      const started = await apiClientV2.post<Run>(`/ontologies/${ontologyId}/what-if/scenarios/${scenario.id}/runs`);
      setRun(started); setStep(3);
    } catch (reason: any) { setError(String(reason?.detail?.message || reason?.message || "推演启动失败")); }
  };
  if (!targetId) return <div className="rounded-xl border border-slate-200 bg-white p-8 text-center"><h2 className="text-sm font-semibold text-slate-900">先选择一个基线观测</h2><p className="mx-auto mt-2 max-w-md text-xs leading-5 text-slate-500">What-If 只对选定的 FactoryNet Observation 及其两跳邻域运行。请先打开数据模型，选择一个观测实例。</p><button onClick={() => navigate(`/ontologies/${ontologyId}?tab=data_model`)} className="mt-5 rounded-md bg-slate-800 px-4 py-2 text-xs text-white">返回数据模型</button></div>;
  if (loading) return <div className="flex min-h-[420px] items-center justify-center text-sm text-slate-500"><Loader2 size={17} className="mr-2 animate-spin" />正在准备推演基线</div>;
  if (error && !context) return <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700"><AlertTriangle size={16} className="mr-2 inline" />{error}</div>;
  if (!context || !target) return <div className="rounded-xl border border-slate-200 bg-white p-8 text-center text-sm text-slate-500">请先在 FactoryNet 数据模型中选择一个 Observation</div>;
  return <div className="space-y-4" data-testid="what-if-tab">
    <section className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center gap-2">{["选择基线", "设置假设", "运行推演", "查看差异"].map((label, index) => <div key={label} className={`flex items-center gap-2 text-xs ${step === index + 1 ? "font-semibold text-slate-900" : step > index + 1 ? "text-emerald-700" : "text-slate-400"}`}><span className={`grid h-6 w-6 place-items-center rounded-full border ${step > index + 1 ? "border-emerald-600 bg-emerald-50" : step === index + 1 ? "border-slate-800 bg-slate-800 text-white" : "border-slate-200"}`}>{step > index + 1 ? <CheckCircle2 size={13} /> : index + 1}</span>{label}{index < 3 && <span className="mx-1 text-slate-300">/</span>}</div>)}</div></section>
    {step === 1 && <section className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="text-sm font-semibold text-slate-900">基线观测</h2><div className="mt-3 grid gap-3 md:grid-cols-4 text-xs"><div><span className="text-slate-500">Observation</span><p className="mt-1 break-all font-mono text-slate-800">{target.id}</p></div><div><span className="text-slate-500">episode_id</span><p className="mt-1 font-mono text-slate-800">{context.episode_id || target.properties?.episode_id || "—"}</p></div><div><span className="text-slate-500">Ordinal</span><p className="mt-1 font-mono text-slate-800">{context.at || target.event_seq || "—"}</p></div><div><span className="text-slate-500">邻域</span><p className="mt-1 text-slate-800">{context.nodes.length} 个节点 / {context.edges.length} 条关系</p></div></div><button onClick={() => setStep(2)} className="mt-5 rounded-md bg-slate-800 px-4 py-2 text-xs text-white">确认基线</button></section>}
    {step === 2 && <section className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="text-sm font-semibold text-slate-900">设置假设</h2><p className="mt-1 text-xs text-slate-500">修改只在当前情景中生效，正式本体和真实实例不会被改写。</p><div className="mt-4 grid max-w-xl gap-3 md:grid-cols-2"><label className="text-xs text-slate-600">当前值<input className="mt-1 w-full rounded-md border border-slate-200 bg-slate-50 px-3 py-2 text-xs" value={String(currentValue)} readOnly /></label><label className="text-xs text-slate-600">情景值<select className="mt-1 w-full rounded-md border border-slate-300 bg-white px-3 py-2 text-xs" value={scenarioValue} onChange={(event) => setScenarioValue(event.target.value)}><option value="worn">worn</option><option value="unworn">unworn</option></select></label></div><div className="mt-5 rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="flex flex-wrap items-center justify-between gap-2"><span className="text-xs font-medium text-slate-700">实例关系（可选）</span><select aria-label="关系假设类型" className="rounded-md border border-slate-300 bg-white px-2 py-1.5 text-[11px]" value={relationMode} onChange={(event) => setRelationMode(event.target.value as RelationMode)}><option value="none">不调整关系</option><option value="add">新增关系</option><option value="remove">移除关系</option></select></div>{relationMode === "add" && <div className="mt-3 grid gap-2 md:grid-cols-3"><select aria-label="新增关系起点" className="rounded-md border border-slate-300 bg-white px-2 py-2 text-[11px]" value={relationSource} onChange={(event) => setRelationSource(event.target.value)}><option value="">起点实例</option>{context.nodes.map((node) => <option key={node.id} value={node.id}>{nodeText(node)}</option>)}</select><select aria-label="新增关系终点" className="rounded-md border border-slate-300 bg-white px-2 py-2 text-[11px]" value={relationTarget} onChange={(event) => setRelationTarget(event.target.value)}><option value="">终点实例</option>{context.nodes.map((node) => <option key={node.id} value={node.id}>{nodeText(node)}</option>)}</select><input aria-label="新增关系名称" className="rounded-md border border-slate-300 bg-white px-2 py-2 text-[11px]" value={relationType} onChange={(event) => setRelationType(event.target.value)} placeholder="关系名称" /></div>}{relationMode === "remove" && <select aria-label="选择要移除的关系" className="mt-3 w-full rounded-md border border-slate-300 bg-white px-2 py-2 text-[11px]" value={relationToRemove} onChange={(event) => setRelationToRemove(event.target.value)}><option value="">选择现有关系</option>{relationOptions.map((edge) => { const key = `${edge.source}|${edge.type || "RELATED"}|${edge.target}`; return <option key={key} value={key}>{String(edge.type || "RELATED")} · {nodeText(context.nodes.find((node) => node.id === edge.source) || { id: edge.source })} → {nodeText(context.nodes.find((node) => node.id === edge.target) || { id: edge.target })}</option>; })}</select>}</div><div className="mt-3 rounded-lg border border-slate-200 bg-white p-3"><label className="flex items-center gap-2 text-xs text-slate-700"><input type="checkbox" checked={temporaryRuleEnabled} onChange={(event) => setTemporaryRuleEnabled(event.target.checked)} />启用情景规则：IN_PHASE 且 HAS_TOOL_CONDITION → PHASE_TOOL_STATE</label>{(context.rules || []).length > 0 && <div className="mt-3 border-t border-slate-100 pt-3"><p className="text-[11px] text-slate-500">停用当前本体规则（仅本情景）</p><div className="mt-2 flex flex-wrap gap-3">{(context.rules || []).map((rule: any) => <label key={rule.id} className="flex items-center gap-1.5 text-[11px] text-slate-600"><input type="checkbox" checked={disabledRuleIds.includes(String(rule.id))} onChange={(event) => setDisabledRuleIds((current) => event.target.checked ? [...new Set([...current, String(rule.id)])] : current.filter((id) => id !== String(rule.id)))} />{rule.name_cn || rule.name || rule.id}</label>)}</div></div>}</div><div className="mt-5 flex gap-2"><button onClick={() => setStep(1)} className="rounded-md border border-slate-300 px-4 py-2 text-xs">返回</button><button onClick={() => setStep(3)} className="rounded-md bg-slate-800 px-4 py-2 text-xs text-white">确认假设</button></div></section>}
    {step >= 3 && <section className="rounded-xl border border-slate-200 bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-slate-900">运行推演</h2><p className="mt-1 text-xs text-slate-500">基线和情景均固定在修订 {context.base_revision_id || "—"}，结果不会写回正式本体。</p></div>{!run && <button onClick={() => void runScenario()} className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-4 py-2 text-xs text-white"><Play size={13} />开始推演</button>}</div>{run && <div className="mt-4 rounded-lg border border-slate-200 bg-slate-50 p-4"><div className="flex items-center gap-2 text-xs text-slate-700">{["queued", "running"].includes(run.status) && <Loader2 size={15} className="animate-spin" />}{run.status === "completed" && <CheckCircle2 size={15} className="text-emerald-700" />}{run.status === "failed" && <AlertTriangle size={15} className="text-red-600" />}<strong>{run.status === "queued" ? "排队中" : run.status === "running" ? (stageNames[run.stage] || "处理中") : run.status === "completed" ? "推演完成" : "推演失败"}</strong><span className="ml-auto tabular-nums">{run.progress}%</span></div><div className="mt-3 h-2 overflow-hidden rounded-full bg-slate-200"><div className="h-full rounded-full bg-slate-700 transition-all" style={{ width: `${run.progress}%` }} /></div>{run.error && <p className="mt-2 text-xs text-red-700">{run.error}</p>}</div>}{run?.status === "completed" && <button onClick={() => setStep(4)} className="mt-4 inline-flex items-center gap-1 rounded-md border border-slate-300 px-4 py-2 text-xs"><RotateCcw size={13} />查看差异</button>}</section>}
    {step === 4 && run?.diff && <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_340px]"><div className="rounded-xl border border-slate-200 bg-white p-5"><div className="flex flex-wrap items-center justify-between gap-2"><h2 className="text-sm font-semibold text-slate-900">本体关系差异</h2><div className="flex items-center gap-2"><span className="text-[11px] text-slate-500">新增 {run.diff.added?.length || 0} · 消失 {run.diff.removed?.length || 0} · 未变化 {run.diff.unchanged?.length || 0}</span><button onClick={() => { setRun(null); setScenarioId(""); setStep(2); }} className="rounded-md border border-slate-300 px-2.5 py-1.5 text-[11px] text-slate-700 hover:border-slate-600">再次配置</button></div></div><ScenarioGraph context={context} diff={run.diff} /><div className="mt-4 space-y-2">{(run.diff.added || []).map((fact) => <div key={`a-${fact}`} className="rounded-md border border-emerald-200 bg-emerald-50 px-3 py-2 font-mono text-xs text-emerald-800">+ {fact}</div>)}{(run.diff.removed || []).map((fact) => <div key={`r-${fact}`} className="rounded-md border border-red-200 bg-red-50 px-3 py-2 font-mono text-xs text-red-800">− {fact}</div>)}{(run.diff.unchanged || []).slice(0, 24).map((fact) => <div key={`u-${fact}`} className="rounded-md border border-slate-200 px-3 py-2 font-mono text-xs text-slate-500">= {fact}</div>)}</div></div><aside className="rounded-xl border border-slate-200 bg-white p-5"><h2 className="text-sm font-semibold text-slate-900">推导与证据</h2><p className="mt-2 text-xs text-slate-500">引擎：{run.engine || "—"}</p><p className="mt-1 break-all text-[10px] text-slate-400">版本：{run.engine_version || "—"}</p><div className="mt-4 space-y-2">{(run.diff.proofs || []).map((proof: any) => <div key={proof.fact} className="rounded-md border border-slate-200 bg-slate-50 p-2 text-[11px]"><p className="font-mono text-slate-800">{proof.fact}</p><p className="mt-1 text-slate-500">规则 {proof.rule_id}</p><p className="mt-1 text-slate-500">前提：{(proof.premises || []).join("；")}</p>{Array.isArray(proof.evidence) && proof.evidence.length > 0 && <p className="mt-1 break-words text-slate-500">证据：{proof.evidence.slice(0, 3).map((item: any) => String(item.evidence_ref_id || item.source_file || item.source_row || item.source_sample_id || item.source || "来源记录")).join("；")}</p>}</div>)}{!(run.diff.proofs || []).length && <p className="text-xs text-slate-400">本次没有新增可执行规则结论</p>}</div></aside></section>}
  </div>;
}
