/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import { CheckCircle2, CirclePause, Loader2, Play, RefreshCw, RotateCcw, SkipForward, Square, TriangleAlert } from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Run = {
  id: string; run_id?: string; ontology_id: string; status: string; source_mode?: string;
  episode_ids?: string[]; series_ids?: string[]; start_ordinal?: number; end_ordinal?: number;
  current_ordinal?: number | null; current_event_index?: number; total_events?: number; committed_events?: number;
  watermark_ordinal?: number | null; event_interval_ms?: number; speed?: number; progress?: { pct?: number; completed?: number; total?: number; stage?: string };
  metrics?: Record<string, unknown>; state?: Record<string, any>; latest_event?: { payload?: Record<string, unknown>; event_key?: string; ordinal?: number; source_row_id?: string; episode_id?: string } | null; error?: string; published_snapshot_id?: string | null;
};
type Graph = { nodes: any[]; edges: any[]; total_instances?: number; total_edges?: number; available?: boolean; error?: string };

const label: Record<string, string> = { created: "待开始", queued: "排队中", running: "处理中", pausing: "即将暂停", paused: "已暂停", completed: "已完成，待发布", published: "已发布", failed: "失败", cancelled: "已取消" };
const fmt = (value: unknown) => value === null || value === undefined || value === "" ? "—" : String(value);
const errorText = (reason: any) => typeof reason?.detail === "string" ? reason.detail : reason?.detail?.message || reason?.message || "动态运行请求失败";

function StreamGraph({
  graph,
  latestEvent,
  onSelectNode,
  onSelectEdge,
}: {
  graph: Graph;
  latestEvent?: Run["latest_event"];
  onSelectNode: (node: any) => void;
  onSelectEdge: (edge: any) => void;
}) {
  const ref = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  useEffect(() => {
    if (!ref.current) return;
    cyRef.current?.destroy();
    const latestId = latestEvent?.episode_id && latestEvent.source_row_id
      ? `FactoryNet:Observation:${latestEvent.episode_id}:${latestEvent.source_row_id}`
      : "";
    const cy = cytoscape({
      container: ref.current,
      elements: [
        ...(graph.nodes || []).map((node) => ({
          data: {
            id: node.id,
            label: String(node.properties?.episode_id || node.properties?.value || node.properties?.machine_type || node.entity_type || node.id).slice(0, 24),
            node,
          },
          classes: node.id === latestId ? "latest" : "",
        })),
        ...(graph.edges || []).filter((edge) => edge.source && edge.target).map((edge) => ({
          data: { id: edge.id, source: edge.source, target: edge.target, label: edge.type || edge.label, edge },
          classes: edge.properties?.valid_to_ordinal !== null && edge.properties?.valid_to_ordinal !== undefined ? "historical" : "",
        })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "background-color": "#0f766e", color: "#0f172a", "font-size": 9, "text-valign": "bottom", "text-margin-y": 5, width: 19, height: 19, "border-width": 1, "border-color": "#ffffff" } },
        { selector: "node.latest", style: { "background-color": "#2563eb", "border-width": 4, "border-color": "#bfdbfe", "overlay-color": "#60a5fa", "overlay-opacity": 0.2, "overlay-padding": 7 } },
        { selector: "edge", style: { width: 1.2, "line-color": "#94a3b8", "target-arrow-color": "#64748b", "target-arrow-shape": "triangle", label: "data(label)", "font-size": 7, color: "#64748b", "curve-style": "bezier" } },
        { selector: "edge.historical", style: { "line-style": "dashed", "line-color": "#94a3b8", "target-arrow-color": "#94a3b8", opacity: 0.55 } },
      ],
      layout: { name: "cose", animate: false, fit: true, padding: 28 },
    });
    cy.on("tap", "node", (event) => onSelectNode(event.target.data("node")));
    cy.on("tap", "edge", (event) => onSelectEdge(event.target.data("edge")));
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, [graph, latestEvent, onSelectEdge, onSelectNode]);
  return <div ref={ref} className="h-[430px] rounded-lg bg-slate-50" />;
}

function KeyValueTable({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values || {}).filter(([key]) => !key.startsWith("_"));
  if (!entries.length) return <p className="text-xs text-slate-400">暂无字段</p>;
  return <div className="overflow-hidden rounded border border-slate-200">{entries.slice(0, 24).map(([key, value]) => <div key={key} className="grid grid-cols-[110px_minmax(0,1fr)] border-b border-slate-100 last:border-0"><span className="bg-slate-50 px-2 py-1.5 text-[11px] text-slate-500">{key}</span><span className="break-words px-2 py-1.5 text-[11px] text-slate-700">{typeof value === "object" ? JSON.stringify(value) : fmt(value)}</span></div>)}</div>;
}

export default function DynamicEvolutionTab({ ontologyId }: { ontologyId: string }) {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run_id") || "";
  const [runs, setRuns] = useState<Run[]>([]);
  const [run, setRun] = useState<Run | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [selectedEdge, setSelectedEdge] = useState<any>(null);
  const [viewMode, setViewMode] = useState<"current" | "history">("current");
  const [followLatest, setFollowLatest] = useState(true);
  const [at, setAt] = useState<number | null>(params.get("at") ? Number(params.get("at")) : null);
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadRuns = useCallback(async () => {
    try { const result = await apiClientV2.get<{ runs?: Run[] }>(`/ontologies/${ontologyId}/temporal-streams`); setRuns(result?.runs || []); } catch (reason) { setError(errorText(reason)); }
  }, [ontologyId]);
  const load = useCallback(async () => {
    if (!runId) { await loadRuns(); return; }
    try {
      const current = await apiClientV2.get<Run>(`/temporal-streams/${runId}`);
      setRun(current); setSpeed(Number(current.speed || 1));
      const point = followLatest ? current.current_ordinal : at;
      if (followLatest && point !== null && point !== undefined) { setAt(Number(point)); setParams((old) => { const next = new URLSearchParams(old); next.set("at", String(point)); return next; }, { replace: true }); }
      const data = await apiClientV2.get<Graph>(`/temporal-streams/${runId}/graph`, { params: { at: point ?? undefined, mode: "cumulative", relation_state: viewMode === "current" ? "current" : "all", limit: 200 } });
      setGraph(data);
      setSelectedEdge(null);
      setError("");
    } catch (reason) { setError(errorText(reason)); }
  }, [at, followLatest, loadRuns, runId, setParams, viewMode]);

  // The first load is intentionally scheduled after the effect commits so the
  // page does not synchronously update state while React is flushing effects.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void load(); }, [load]);
  useEffect(() => { if (!runId || !run || ["failed", "cancelled", "published", "completed"].includes(run.status)) return; const timer = window.setInterval(() => void load(), 900); return () => window.clearInterval(timer); }, [load, run, runId]);
  const loadRef = useRef(load);
  useEffect(() => { loadRef.current = load; }, [load]);
  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") return;
    let source: EventSource | null = null;
    let retryTimer: number | null = null;
    let stopped = false;
    const handle = (event: MessageEvent) => {
      try {
        const parsed = JSON.parse(event.data) as Run & { run?: Run };
        const payload = parsed.run || parsed;
        setRun(payload);
        if (["completed", "failed", "cancelled", "published"].includes(payload.status)) {
          stopped = true;
          source?.close();
        }
        if (followLatest && payload.current_ordinal !== null && payload.current_ordinal !== undefined) setAt(Number(payload.current_ordinal));
        void loadRef.current();
      } catch { /* polling remains the fallback */ }
    };
    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/v2/temporal-streams/${runId}/event-stream`);
      source.addEventListener("update", handle);
      source.addEventListener("completed", handle);
      source.addEventListener("event_committed", handle);
      source.onerror = () => {
        source?.close();
        source = null;
        if (!stopped) retryTimer = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retryTimer !== null) window.clearTimeout(retryTimer);
      source?.close();
    };
  }, [followLatest, runId]);

  const control = async (action: string) => { if (!runId) return; setBusy(action); setError(""); try { await apiClientV2.post(`/temporal-streams/${runId}/control`, { action, speed }); await load(); } catch (reason) { setError(errorText(reason)); } finally { setBusy(""); } };
  const create = async () => { setBusy("create"); setError(""); try { const created = await apiClientV2.post<Run>(`/ontologies/${ontologyId}/temporal-streams`, { source_id: "factorynet_cnc", source_mode: "file_replay", speed: 1 }); setParams((old) => { const next = new URLSearchParams(old); next.set("tab", "dynamic"); next.set("run_id", created.id || created.run_id || ""); return next; }); } catch (reason) { setError(errorText(reason)); } finally { setBusy(""); } };
  const publish = async () => { if (!runId) return; setBusy("publish"); setError(""); try { await apiClientV2.post(`/temporal-streams/${runId}/publish`); await load(); } catch (reason) { setError(errorText(reason)); } finally { setBusy(""); } };
  const updateAt = (value: number) => { setFollowLatest(false); setAt(value); setParams((old) => { const next = new URLSearchParams(old); next.set("at", String(value)); return next; }, { replace: true }); };
  const latestPayload = run?.latest_event?.payload || {};
  const stateRows = useMemo(() => Object.entries((run?.state?.episodes || {})[run?.episode_ids?.[0] || ""] || {}).filter(([key]) => !key.endsWith("previous_fact_id")), [run]);
  const pushExample = useMemo(() => JSON.stringify({ event_id: "factorynet-demo-001", episode_id: run?.episode_ids?.[0] || "episode-001", entity_key: "CNC_Mill_3_Axis", ordinal: Number(run?.current_ordinal || 0) + 1, source_sequence: Number(run?.current_event_index ?? -1) + 1, payload: { time_s: Number(run?.current_ordinal || 0) + 1, ctx_process_phase: "roughing", ctx_tool_condition: "unworn", ctx_passed_visual_inspection: true }, source_ref: { source_row_id: "demo-001", source: "script" } }, null, 2), [run]);

  if (!runId) return <div className="space-y-4" data-testid="dynamic-evolution-tab"><section className="rounded-xl border border-slate-200 bg-white p-6"><div className="flex items-start justify-between gap-3"><div><p className="text-xs uppercase tracking-[0.16em] text-slate-400">FactoryNet CNC</p><h2 className="mt-1 text-lg font-semibold text-slate-900">动态演化</h2><p className="mt-2 text-xs text-slate-500">每次提交一条观测，实例关系随 Ordinal 前进；完成后由你确认发布快照。</p></div><button onClick={() => void create()} disabled={!!busy} className="inline-flex items-center gap-2 rounded-md bg-slate-900 px-3 py-2 text-xs text-white disabled:opacity-50"><Play size={13} />{busy === "create" ? "创建中" : "新建动态运行"}</button></div>{error && <p className="mt-4 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700">{error}</p>}</section><section className="rounded-xl border border-slate-200 bg-white p-5"><h3 className="text-sm font-medium">已有运行</h3><div className="mt-3 space-y-2">{runs.map((item) => <button key={item.id} onClick={() => { const next = new URLSearchParams(params); next.set("run_id", item.id); setParams(next); }} className="flex w-full items-center justify-between rounded border border-slate-200 px-3 py-3 text-left text-xs hover:border-slate-400"><span><span className="font-mono text-slate-700">{item.id.slice(0, 8)}</span><span className="ml-3 text-slate-500">{(item.series_ids || item.episode_ids || []).join(", ") || "推送模式"}</span></span><span className="text-slate-500">{label[item.status] || item.status}</span></button>)}{!runs.length && <p className="text-xs text-slate-400">暂无动态运行，点击右上角创建演示。</p>}</div></section></div>;
  if (!run) return <div className="flex min-h-[360px] items-center justify-center text-sm text-slate-500"><Loader2 className="mr-2 animate-spin" size={17} />读取动态运行</div>;
  const total = Number(run.total_events || run.progress?.total || 0); const committed = Number(run.committed_events || run.progress?.completed || 0); const pct = Number(run.progress?.pct ?? (total ? committed / total * 100 : 0)); const start = Number(run.start_ordinal ?? 0); const end = Number(run.end_ordinal ?? start); const point = at ?? Number(run.current_ordinal ?? start);
  return <div className="space-y-4" data-testid="dynamic-evolution-tab">
    <div className="flex flex-wrap items-start justify-between gap-3"><div><button onClick={() => { const next = new URLSearchParams(params); next.delete("run_id"); setParams(next); }} className="mb-2 text-xs text-slate-500 hover:text-slate-900">返回运行列表</button><h2 className="text-xl font-semibold text-slate-900">动态演化</h2><p className="mt-1 text-xs text-slate-500">运行 {run.id.slice(0, 12)} · episode：{(run.episode_ids || run.series_ids || []).join(", ") || "推送"} · 时间语义：Ordinal</p></div><button onClick={() => void load()} className="inline-flex items-center gap-2 rounded border border-slate-300 bg-white px-3 py-2 text-xs hover:border-slate-500"><RefreshCw size={13} />刷新</button></div>
    {error && <div className="flex items-start gap-2 rounded border border-red-200 bg-red-50 px-3 py-2 text-xs text-red-700"><TriangleAlert size={14} />{error}</div>}
    <section className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-2"><span className={`grid h-8 w-8 place-items-center rounded-full ${run.status === "failed" ? "bg-red-100 text-red-700" : run.status === "completed" || run.status === "published" ? "bg-emerald-100 text-emerald-700" : "bg-slate-100 text-slate-700"}`}>{run.status === "completed" || run.status === "published" ? <CheckCircle2 size={16} /> : run.status === "failed" ? <TriangleAlert size={16} /> : <Loader2 className="animate-spin" size={16} />}</span><div><p className="text-sm font-medium text-slate-900">{label[run.status] || run.status}</p><p className="mt-0.5 text-xs text-slate-500">{run.error || run.progress?.stage || `已提交 ${committed} / ${total} 条事件`}</p></div></div><div className="flex flex-wrap items-center gap-2"><label className="text-xs text-slate-500">速度<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} className="ml-1 rounded border border-slate-300 px-2 py-1"><option value={0.5}>0.5 条/秒</option><option value={1}>1 条/秒</option><option value={2}>2 条/秒</option><option value={5}>5 条/秒</option><option value={10}>10 条/秒</option><option value={20}>20 条/秒</option></select></label><button disabled={!!busy || !["created", "paused", "queued"].includes(run.status)} onClick={() => void control(run.status === "paused" ? "resume" : "start")} className="rounded border border-slate-300 px-2 py-1.5 text-xs disabled:opacity-40"><Play size={12} className="mr-1 inline" />{run.status === "paused" ? "继续" : "开始"}</button><button disabled={!!busy || !["running", "pausing"].includes(run.status)} onClick={() => void control("pause")} className="rounded border border-slate-300 px-2 py-1.5 text-xs disabled:opacity-40"><CirclePause size={12} className="mr-1 inline" />暂停</button><button disabled={!!busy || !["created", "paused"].includes(run.status)} onClick={() => void control("step")} className="rounded border border-slate-300 px-2 py-1.5 text-xs disabled:opacity-40"><SkipForward size={12} className="mr-1 inline" />单步</button><button disabled={!!busy || ["completed", "published", "failed", "cancelled"].includes(run.status)} onClick={() => void control("cancel")} className="rounded border border-red-200 px-2 py-1.5 text-xs text-red-700 disabled:opacity-40"><Square size={11} className="mr-1 inline" />取消</button></div></div><div className="mt-4 h-2 overflow-hidden rounded bg-slate-100"><div className="h-full bg-slate-800 transition-all" style={{ width: `${Math.min(100, Math.max(0, pct))}%` }} /></div><div className="mt-2 flex flex-wrap justify-between gap-2 text-[11px] text-slate-500"><span>当前事件 {committed} / {total} · 队列 {Math.max(0, total - committed)}</span><span>水位 Ordinal：{fmt(run.watermark_ordinal)}</span></div></section>
    <section className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="text-sm font-medium">Ordinal 时间轴</h3><p className="mt-1 text-[11px] text-slate-500">拖动只改变查看位置，不会修改运行水位。</p></div><div className="flex items-center gap-2"><button onClick={() => { setFollowLatest(true); updateAt(Number(run.current_ordinal ?? end)); }} className={`rounded border px-2 py-1 text-[11px] ${followLatest ? "border-slate-800 bg-slate-800 text-white" : "border-slate-300"}`}>跟随最新</button><button onClick={() => { setAt(start); setFollowLatest(false); }} className="rounded border border-slate-300 p-1.5" title="重置位置"><RotateCcw size={13} /></button></div></div><input aria-label="Ordinal 时间轴" type="range" min={start} max={Math.max(start, end)} step="any" value={point} onChange={(event) => updateAt(Number(event.target.value))} className="mt-4 w-full accent-slate-800" /><div className="mt-1 flex justify-between text-[11px] text-slate-400"><span>{fmt(start)}</span><span>当前位置 {fmt(point)}</span><span>{fmt(end)}</span></div></section>
    <section className="grid grid-cols-2 gap-3 md:grid-cols-5">{([["已提交事件", committed], ["当前 Ordinal", fmt(run.current_ordinal)], ["图中节点", graph?.total_instances ?? "—"], ["图中关系", graph?.total_edges ?? "—"], ["状态迁移", run.metrics?.state_transitions ?? 0]] as Array<[string, unknown]>).map(([key, value]) => <div key={key} className="rounded-xl border border-slate-200 bg-white p-3"><p className="text-[11px] text-slate-500">{key}</p><p className="mt-1 text-lg font-semibold tabular-nums text-slate-900">{fmt(value)}</p></div>)}</section>
    <section className="grid gap-4 xl:grid-cols-[minmax(0,1fr)_330px]"><div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-medium">动态实例关系</h3><p className="mt-1 text-[11px] text-slate-500">新增观测逐条进入；选择“完整历史”可查看已失效事实。</p></div><div className="flex items-center gap-3"><span className="inline-flex items-center gap-1 text-[10px] text-slate-400"><span className="h-2 w-2 rounded-full bg-blue-600" />最新事件</span><span className="inline-flex items-center gap-1 text-[10px] text-slate-400"><span className="h-px w-4 border-t border-dashed border-slate-400" />历史事实</span><div className="flex gap-1 rounded border border-slate-200 p-0.5"><button onClick={() => setViewMode("current")} className={`rounded px-2 py-1 text-[11px] ${viewMode === "current" ? "bg-slate-800 text-white" : "text-slate-500"}`}>当前状态</button><button onClick={() => setViewMode("history")} className={`rounded px-2 py-1 text-[11px] ${viewMode === "history" ? "bg-slate-800 text-white" : "text-slate-500"}`}>完整历史</button></div></div></div>{graph ? <StreamGraph graph={graph} latestEvent={run.latest_event} onSelectNode={(node) => { setSelectedNode(node); setSelectedEdge(null); }} onSelectEdge={(edge) => { setSelectedEdge(edge); setSelectedNode(null); }} /> : <div className="grid h-[430px] place-items-center text-xs text-slate-400">等待第一条事件提交</div>}</div><aside className="space-y-4"><div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="text-sm font-medium">最新观测</h3>{run.latest_event ? <div className="mt-3 space-y-2 text-[11px]"><p className="break-all font-mono text-slate-600">{run.latest_event.event_key}</p><p>episode：{fmt(run.latest_event.episode_id)} · Ordinal：{fmt(run.latest_event.ordinal)}</p><KeyValueTable values={latestPayload} /></div> : <p className="mt-3 text-xs text-slate-400">尚未提交事件</p>}</div><div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="text-sm font-medium">当前状态</h3><div className="mt-3 space-y-2">{stateRows.map(([key, value]) => { const item = value && typeof value === "object" ? value as Record<string, unknown> : null; return <div key={key} className="rounded border border-slate-100 bg-slate-50 p-2 text-[11px]"><span className="text-slate-500">{key}</span><p className="mt-1 break-words text-slate-800">{item ? fmt(item.value || item.object_id) : fmt(value)}</p></div>; })}{!stateRows.length && <p className="text-xs text-slate-400">暂无状态迁移</p>}</div></div>{selectedNode && <div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="text-sm font-medium">选中实例</h3><p className="mt-2 break-all font-mono text-[10px] text-slate-500">{selectedNode.id}</p><p className="mt-1 text-[11px] text-slate-600">类型：{selectedNode.entity_type}</p><div className="mt-2"><KeyValueTable values={selectedNode.properties || {}} /></div></div>}{selectedEdge && <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex items-start justify-between gap-2"><div><h3 className="text-sm font-medium">关系检查器</h3><p className="mt-1 break-all font-mono text-[10px] text-slate-500">{selectedEdge.id}</p></div><button onClick={() => setSelectedEdge(null)} className="text-slate-400 hover:text-slate-700" title="关闭"><span aria-hidden="true">×</span></button></div><div className="mt-3 space-y-1 text-[11px] text-slate-600"><p>关系：{selectedEdge.type || selectedEdge.label || "关联"}</p><p>起点：{selectedEdge.source}</p><p>终点：{selectedEdge.target}</p><p>有效区间：{fmt(selectedEdge.properties?.valid_from_ordinal)} → {fmt(selectedEdge.properties?.valid_to_ordinal)}{selectedEdge.properties?.valid_to_ordinal !== null && selectedEdge.properties?.valid_to_ordinal !== undefined ? "（已失效）" : "（当前有效）"}</p><p>来源事件：<span className="font-mono">{fmt(selectedEdge.properties?.source_event_id || selectedEdge.properties?._event_id)}</span></p><p>EvidenceRef：<span className="font-mono">{fmt(selectedEdge.properties?.evidence_ref_id)}</span></p></div><div className="mt-3"><KeyValueTable values={selectedEdge.properties || {}} /></div></div>}</aside></section>
    {run.status === "completed" && <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3"><div><p className="text-sm font-medium text-emerald-900">事件已全部处理</p><p className="mt-1 text-xs text-emerald-800">发布前会再次校验事件、事实有效区间和当前状态唯一性。</p></div><button onClick={() => void publish()} disabled={!!busy} className="rounded bg-emerald-700 px-3 py-2 text-xs text-white disabled:opacity-50">{busy === "publish" ? "发布中" : "发布数据模型快照"}</button></section>}
    <section className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-sm font-medium">事件推送接口</h3><p className="mt-1 text-[11px] text-slate-500">推送模式接收结构化事件；文件回放也使用相同的事件契约。</p></div><span className="rounded bg-slate-100 px-2 py-1 text-[10px] text-slate-500">POST /api/v2/temporal-streams/{run.id}/events</span></div><pre className="mt-3 max-h-44 overflow-auto rounded-lg bg-slate-950 p-3 text-[10px] leading-5 text-slate-100">{pushExample}</pre><div className="mt-3 flex flex-wrap gap-3 text-[11px] text-slate-500"><span>最近接收：{fmt(run.latest_event?.event_key)}</span><span>事件状态：{run.latest_event ? "已提交" : "尚未接收"}</span><span>重复 event_id + 相同载荷会幂等处理</span></div></section>
    {run.status === "completed" && <section className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="text-sm font-medium">发布校验摘要</h3><div className="mt-3 grid grid-cols-2 gap-2 text-[11px] md:grid-cols-5"><div className="rounded bg-slate-50 p-2"><span className="text-slate-500">事件</span><strong className="mt-1 block text-slate-800">{committed} / {total}</strong></div><div className="rounded bg-slate-50 p-2"><span className="text-slate-500">节点</span><strong className="mt-1 block text-slate-800">{fmt(graph?.total_instances)}</strong></div><div className="rounded bg-slate-50 p-2"><span className="text-slate-500">关系</span><strong className="mt-1 block text-slate-800">{fmt(graph?.total_edges)}</strong></div><div className="rounded bg-slate-50 p-2"><span className="text-slate-500">状态迁移</span><strong className="mt-1 block text-slate-800">{fmt(run.metrics?.state_transitions ?? 0)}</strong></div><div className="rounded bg-slate-50 p-2"><span className="text-slate-500">证据</span><strong className="mt-1 block text-slate-800">{fmt(run.metrics?.evidence_count ?? "待发布校验")}</strong></div></div></section>}
    {run.status === "published" && <section className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-xs text-slate-600">快照已发布：<span className="font-mono">{run.published_snapshot_id || "—"}</span>。正式“数据模型”将读取该运行的独立图空间。</section>}
  </div>;
}
