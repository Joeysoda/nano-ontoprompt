/* eslint-disable @typescript-eslint/no-explicit-any */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import {
  Activity,
  CheckCircle2,
  ChevronDown,
  ChevronUp,
  CirclePause,
  History,
  Loader2,
  Play,
  RefreshCw,
  SkipForward,
  Square,
  TriangleAlert,
} from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Run = {
  id: string;
  run_id?: string;
  ontology_id?: string;
  status: string;
  source_mode?: string;
  episode_ids?: string[];
  current_ordinal?: number | null;
  watermark_ordinal?: number | null;
  current_event_index?: number;
  committed_events?: number;
  received_events?: number;
  horizon_known?: boolean;
  first_received_ordinal?: number | null;
  last_received_at?: string | null;
  source_exhausted?: boolean;
  speed?: number;
  state?: Record<string, any>;
  metrics?: Record<string, any>;
  latest_event?: {
    event_key?: string;
    episode_id?: string;
    ordinal?: number;
    source_row_id?: string;
    payload?: Record<string, unknown>;
  } | null;
  error?: string;
  published_snapshot_id?: string | null;
};

type GraphNode = {
  id: string;
  entity_type?: string;
  properties?: Record<string, unknown>;
  node_kind?: string;
};

type GraphEdge = {
  id: string;
  source: string;
  target: string;
  type?: string;
  label?: string;
  properties?: Record<string, any>;
};

type Graph = {
  nodes: GraphNode[];
  edges: GraphEdge[];
  total_instances?: number;
  total_edges?: number;
  next_offset?: number | null;
  at?: number | null;
  relation_state?: string;
};

type Readiness = {
  source_ready?: boolean;
  dataset_name?: string;
  ontology_ready?: boolean;
  ontology_name?: string | null;
  ontology_id?: string | null;
  horizon_known?: boolean;
};

const formatValue = (value: unknown) =>
  value === null || value === undefined || value === "" ? "—" : String(value);

const errorText = (reason: any) => {
  const detail = reason?.response?.data?.detail || reason?.detail;
  return detail?.message || detail || reason?.message || "动态数据请求失败";
};

const statusText: Record<string, string> = {
  created: "等待开始",
  queued: "准备接收",
  running: "正在接收",
  pausing: "即将暂停",
  paused: "已暂停",
  completed: "数据源已结束",
  published: "快照已发布",
  failed: "接收失败",
  cancelled: "已取消",
};

function KeyValueTable({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values || {}).filter(([key]) => !key.startsWith("_"));
  if (!entries.length) return <p className="text-xs text-slate-400">暂无字段</p>;
  return (
    <div className="overflow-hidden rounded border border-slate-200">
      {entries.map(([key, value]) => (
        <div key={key} className="grid grid-cols-[120px_minmax(0,1fr)] border-b border-slate-100 last:border-0">
          <span className="bg-slate-50 px-2 py-1.5 text-[11px] text-slate-500">{key}</span>
          <span className="break-words px-2 py-1.5 text-[11px] text-slate-700">
            {typeof value === "object" ? JSON.stringify(value) : formatValue(value)}
          </span>
        </div>
      ))}
    </div>
  );
}

function LiveGraph({
  graph,
  latestEvent,
  onNode,
  onEdge,
}: {
  graph: Graph | null;
  latestEvent?: Run["latest_event"];
  onNode: (node: GraphNode) => void;
  onEdge: (edge: GraphEdge) => void;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    if (!containerRef.current) return;
    const cy = cyRef.current || cytoscape({
      container: containerRef.current,
      elements: [],
      style: [
        { selector: "node", style: { label: "data(label)", "background-color": "#0f766e", color: "#0f172a", "font-size": 9, "text-valign": "bottom", "text-margin-y": 5, width: 20, height: 20, "border-width": 1, "border-color": "#ffffff" } },
        { selector: "node.latest", style: { "background-color": "#2563eb", "border-width": 4, "border-color": "#bfdbfe", "overlay-color": "#60a5fa", "overlay-opacity": 0.28, "overlay-padding": 8 } },
        { selector: "edge", style: { width: 1.2, "line-color": "#94a3b8", "target-arrow-color": "#64748b", "target-arrow-shape": "triangle", label: "data(label)", "font-size": 7, color: "#64748b", "curve-style": "bezier" } },
        { selector: "edge.historical", style: { "line-style": "dashed", "line-color": "#94a3b8", "target-arrow-color": "#94a3b8", opacity: 0.55 } },
        { selector: "edge.transition", style: { "line-color": "#2563eb", "target-arrow-color": "#2563eb", width: 2 } },
      ],
    });
    if (!cyRef.current) {
      cy.on("tap", "node", (event) => onNode(event.target.data("node")));
      cy.on("tap", "edge", (event) => onEdge(event.target.data("edge")));
      cyRef.current = cy;
    }
    const resize = () => {
      cy.resize();
      if (cy.elements().length) cy.fit(undefined, 32);
    };
    const observer = new ResizeObserver(resize);
    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [onEdge, onNode]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !graph) return;
    const nodeIds = new Set(graph.nodes.map((node) => node.id));
    const edgeIds = new Set(graph.edges.map((edge) => edge.id));
    cy.nodes().filter((node) => !nodeIds.has(String(node.id()))).remove();
    cy.edges().filter((edge) => !edgeIds.has(String(edge.id()))).remove();
    const latestId = latestEvent?.episode_id && latestEvent.source_row_id
      ? `FactoryNet:Observation:${latestEvent.episode_id}:${latestEvent.source_row_id}`
      : "";
    graph.nodes.forEach((node) => {
      const existing = cy.getElementById(node.id);
      const data = {
        id: node.id,
        label: String(node.properties?.episode_id || node.properties?.value || node.properties?.machine_type || node.entity_type || node.id).slice(0, 24),
        node,
      };
      if (existing.length) {
        existing.data(data);
        existing.toggleClass("latest", node.id === latestId);
      } else {
        cy.add({ data, classes: node.id === latestId ? "latest" : "" });
      }
    });
    graph.edges.forEach((edge) => {
      if (!edge.source || !edge.target) return;
      const existing = cy.getElementById(edge.id);
      const historical = edge.properties?.valid_to_ordinal !== null && edge.properties?.valid_to_ordinal !== undefined;
      const transition = Boolean(edge.properties?.state_key);
      const classes = `${historical ? "historical" : ""} ${transition ? "transition" : ""}`.trim();
      const data = { id: edge.id, source: edge.source, target: edge.target, label: edge.type || edge.label, edge };
      if (existing.length) {
        existing.data(data);
        existing.classes(classes);
      } else {
        cy.add({ data, classes });
      }
    });
    if (cy.elements().length) {
      cy.layout({ name: "cose", animate: false, fit: true, padding: 32 }).run();
      cy.resize();
      cy.fit(undefined, 32);
    }
  }, [graph, latestEvent]);

  return <div ref={containerRef} className="min-h-[520px] flex-1 rounded-lg bg-slate-50" />;
}

async function loadAllGraph(runId: string, params: Record<string, unknown>): Promise<Graph> {
  const nodes: GraphNode[] = [];
  const edges: GraphEdge[] = [];
  let offset = 0;
  let lastPage: Graph | undefined;
  while (true) {
    const page = await apiClientV2.get<Graph>(`/temporal-streams/${runId}/graph`, {
      params: { ...params, offset, limit: 500 },
    });
    lastPage = page;
    nodes.push(...(page.nodes || []));
    edges.push(...(page.edges || []));
    if (page.next_offset === null || page.next_offset === undefined || !page.nodes?.length) break;
    offset = Number(page.next_offset);
  }
  return { nodes, edges, total_instances: Number(lastPage?.total_instances || nodes.length), total_edges: Number(lastPage?.total_edges || edges.length), ...params } as Graph;
}

export default function DynamicDataPage() {
  const [params, setParams] = useSearchParams();
  const runId = params.get("run_id") || "";
  const [readiness, setReadiness] = useState<Readiness | null>(null);
  const [run, setRun] = useState<Run | null>(null);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [selectedNode, setSelectedNode] = useState<GraphNode | null>(null);
  const [selectedEdge, setSelectedEdge] = useState<GraphEdge | null>(null);
  const [showHistory, setShowHistory] = useState(false);
  const [historyAt, setHistoryAt] = useState<number | null>(null);
  const [historyMode, setHistoryMode] = useState<"valid" | "all">("valid");
  const [speed, setSpeed] = useState(1);
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadReadiness = useCallback(async () => {
    try {
      setReadiness(await apiClientV2.get<Readiness>("/dynamic-data/demo-readiness"));
    } catch (reason) {
      setError(errorText(reason));
    }
  }, []);

  const loadGraph = useCallback(async (current: Run) => {
    const at = showHistory && historyAt !== null ? historyAt : undefined;
    const data = await loadAllGraph(current.id, {
      at,
      mode: "cumulative",
      relation_state: showHistory && historyMode === "all" ? "all" : "current",
      limit: 500,
    });
    setGraph(data);
  }, [historyAt, historyMode, showHistory]);

  const loadRun = useCallback(async () => {
    if (!runId) {
      await loadReadiness();
      return;
    }
    try {
      const current = await apiClientV2.get<Run>(`/temporal-streams/${runId}`);
      setRun(current);
      setSpeed(Number(current.speed || 1));
      if (historyAt === null && current.current_ordinal !== null && current.current_ordinal !== undefined) setHistoryAt(Number(current.current_ordinal));
      await loadGraph(current);
      setError("");
    } catch (reason) {
      setError(errorText(reason));
    }
  }, [historyAt, loadGraph, loadReadiness, runId]);

  // The effect performs an asynchronous server synchronization; the state
  // updates happen after the request resolves rather than during render.
  // eslint-disable-next-line react-hooks/set-state-in-effect
  useEffect(() => { void loadRun(); }, [loadRun]);
  useEffect(() => {
    if (!runId || !run || ["completed", "failed", "cancelled", "published"].includes(run.status)) return;
    const timer = window.setInterval(() => void loadRun(), 900);
    return () => window.clearInterval(timer);
  }, [loadRun, run, runId]);

  useEffect(() => {
    if (!runId || typeof EventSource === "undefined") return;
    let source: EventSource | null = null;
    let retry: number | null = null;
    let stopped = false;
    const refresh = () => void loadRun();
    const connect = () => {
      if (stopped) return;
      source = new EventSource(`/api/v2/temporal-streams/${runId}/event-stream`);
      source.addEventListener("event_committed", refresh);
      source.addEventListener("update", refresh);
      source.addEventListener("completed", refresh);
      source.onerror = () => {
        source?.close();
        source = null;
        if (!stopped) retry = window.setTimeout(connect, 1500);
      };
    };
    connect();
    return () => {
      stopped = true;
      if (retry !== null) window.clearTimeout(retry);
      source?.close();
    };
  }, [loadRun, runId]);

  const create = async () => {
    setBusy("create");
    setError("");
    try {
      const created = await apiClientV2.post<Run>("/dynamic-data/demo-sessions", { speed: 1 });
      const next = new URLSearchParams(params);
      next.set("run_id", created.id || created.run_id || "");
      setParams(next);
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy("");
    }
  };

  const control = async (action: string) => {
    if (!run) return;
    setBusy(action);
    setError("");
    try {
      await apiClientV2.post(`/temporal-streams/${run.id}/control`, { action, speed });
      await loadRun();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy("");
    }
  };

  const publish = async () => {
    if (!run) return;
    setBusy("publish");
    try {
      await apiClientV2.post(`/temporal-streams/${run.id}/publish`);
      await loadRun();
    } catch (reason) {
      setError(errorText(reason));
    } finally {
      setBusy("");
    }
  };

  const restartDemo = () => {
    const next = new URLSearchParams(params);
    next.delete("run_id");
    setRun(null);
    setGraph(null);
    setSelectedNode(null);
    setSelectedEdge(null);
    setShowHistory(false);
    setHistoryAt(null);
    setParams(next);
  };

  const latestPayload = run?.latest_event?.payload || {};
  const stateRows = useMemo(
    () => Object.entries((run?.state?.episodes || {})[run?.episode_ids?.[0] || ""] || {}).filter(([key]) => !key.endsWith("previous_fact_id")),
    [run],
  );
  const first = run?.first_received_ordinal ?? null;
  const latest = run?.watermark_ordinal ?? run?.current_ordinal ?? null;
  const live = run && ["created", "queued", "running", "pausing", "paused"].includes(run.status);
  const statusTone = run?.status === "failed" ? "text-red-700 bg-red-50 border-red-200" : run?.status === "completed" || run?.status === "published" ? "text-emerald-700 bg-emerald-50 border-emerald-200" : "text-slate-700 bg-slate-50 border-slate-200";

  if (!runId || !run) {
    return (
      <div className="wb-page max-w-[1320px] space-y-5" data-testid="dynamic-data-page">
        <header className="wb-page-header">
          <div>
            <p className="wb-eyebrow"><Activity size={13} /> 数据构筑 / 动态数据构建</p>
            <h1 className="wb-page-title mt-2">FactoryNet 实时演示</h1>
            <p className="wb-page-subtitle">从空白开始，按事件逐条接收 FactoryNet 观测。</p>
          </div>
          <button onClick={() => void create()} disabled={busy === "create"} className="wb-button-primary">
            {busy === "create" ? <Loader2 size={14} className="animate-spin" /> : <Play size={14} />}
            {busy === "create" ? "准备中" : "创建演示"}
          </button>
        </header>
        {error && <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700"><TriangleAlert size={14} />{error}</div>}
        <section className="wb-surface p-6">
          <div className="flex items-start gap-4">
            <span className="grid h-11 w-11 shrink-0 place-items-center rounded-xl bg-teal-50 text-teal-700"><Activity size={20} /></span>
            <div className="min-w-0 flex-1">
              <h2 className="text-base font-semibold text-slate-900">逐事件数据流</h2>
              <p className="mt-2 max-w-2xl text-sm leading-6 text-slate-600">创建后先保持空白。点击开始或单步时，系统才接收第一条真实记录；运行中不显示来源文件的总量和未来终点。</p>
              <div className="mt-4 grid gap-3 text-xs text-slate-500 md:grid-cols-3">
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3"><strong className="block text-slate-800">来源</strong><span>{readiness?.dataset_name || "FactoryNet CNC"}</span></div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3"><strong className="block text-slate-800">时间语义</strong><span>Ordinal · time_s</span></div>
                <div className="rounded-lg border border-slate-200 bg-slate-50 p-3"><strong className="block text-slate-800">本体结构</strong><span>{readiness?.ontology_name || "自动选择 FactoryNet 本体"}</span></div>
              </div>
            </div>
          </div>
        </section>
        <section className="wb-surface p-5">
          <h2 className="text-sm font-medium text-slate-900">演示顺序</h2>
          <div className="mt-4 grid gap-3 md:grid-cols-4">
            {["创建后保持空白", "单步接收第一条", "开始并观察关系增长", "历史记录回看已到达事实"].map((item, index) => <div key={item} className="flex gap-3 rounded-lg border border-slate-200 p-3"><span className="grid h-6 w-6 shrink-0 place-items-center rounded-full bg-slate-900 text-[11px] text-white">{index + 1}</span><span className="text-xs leading-5 text-slate-600">{item}</span></div>)}
          </div>
        </section>
      </div>
    );
  }

  return (
    <div className="wb-page max-w-[1500px] space-y-4" data-testid="dynamic-data-page">
      <header className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <button onClick={() => { const next = new URLSearchParams(params); next.delete("run_id"); setParams(next); }} className="mb-2 text-xs text-slate-500 hover:text-slate-900">返回动态数据构建</button>
          <p className="wb-eyebrow"><Activity size={13} /> FactoryNet CNC</p>
          <h1 className="wb-page-title mt-2">动态数据构建</h1>
          <p className="mt-1 text-xs text-slate-500">episode：{run.episode_ids?.join(", ") || "—"} · 时间语义：Ordinal</p>
        </div>
        <button onClick={() => void loadRun()} className="wb-button-secondary"><RefreshCw size={14} />刷新</button>
      </header>
      {error && <div className="flex items-start gap-2 rounded-lg border border-red-200 bg-red-50 px-4 py-3 text-xs text-red-700"><TriangleAlert size={14} />{error}</div>}

      <section className={`flex flex-wrap items-center justify-between gap-3 rounded-xl border px-4 py-3 ${statusTone}`}>
        <div className="flex items-center gap-3">
          <span className={`h-3 w-3 rounded-full ${live && run.status !== "paused" ? "animate-pulse bg-emerald-500" : run.status === "failed" ? "bg-red-500" : "bg-slate-400"}`} />
          <div><strong className="text-sm">{statusText[run.status] || run.status}</strong><p className="mt-0.5 text-[11px] opacity-80">{run.error || (run.status === "created" ? "点击开始后接收第一条数据" : run.status === "completed" ? "当前运行已接收完来源数据" : "只显示已经到达的事实")}</p></div>
        </div>
        <div className="flex flex-wrap items-center gap-2">
          <label className="text-xs">速度<select value={speed} onChange={(event) => setSpeed(Number(event.target.value))} className="ml-1 rounded border border-current/20 bg-white/70 px-2 py-1"><option value={0.5}>0.5 条/秒</option><option value={1}>1 条/秒</option><option value={2}>2 条/秒</option><option value={5}>5 条/秒</option><option value={10}>10 条/秒</option><option value={20}>20 条/秒</option></select></label>
          <button disabled={!!busy || !["created", "paused", "queued"].includes(run.status)} onClick={() => void control(run.status === "paused" ? "resume" : "start")} className="rounded border border-current/20 bg-white/70 px-2.5 py-1.5 text-xs disabled:opacity-40"><Play size={12} className="mr-1 inline" />{run.status === "paused" ? "继续" : "开始"}</button>
          <button disabled={!!busy || !["running", "pausing"].includes(run.status)} onClick={() => void control("pause")} className="rounded border border-current/20 bg-white/70 px-2.5 py-1.5 text-xs disabled:opacity-40"><CirclePause size={12} className="mr-1 inline" />暂停</button>
          <button disabled={!!busy || !["created", "paused"].includes(run.status)} onClick={() => void control("step")} className="rounded border border-current/20 bg-white/70 px-2.5 py-1.5 text-xs disabled:opacity-40"><SkipForward size={12} className="mr-1 inline" />单步</button>
          <button disabled={!!busy || ["completed", "published", "failed", "cancelled"].includes(run.status)} onClick={() => void control("cancel")} className="rounded border border-red-300 bg-white/70 px-2.5 py-1.5 text-xs text-red-700 disabled:opacity-40"><Square size={11} className="mr-1 inline" />取消</button>
        </div>
      </section>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-5">
        {([["已接收", run.received_events ?? run.committed_events ?? 0], ["当前 Ordinal", formatValue(run.current_ordinal)], ["图中节点", graph?.total_instances ?? "—"], ["图中关系", graph?.total_edges ?? "—"], ["状态迁移", run.metrics?.state_transitions ?? 0]] as Array<[string, unknown]>).map(([key, value]) => <div key={key} className="wb-surface p-3"><p className="text-[11px] text-slate-500">{key}</p><p className="mt-1 text-lg font-semibold tabular-nums text-slate-900">{formatValue(value)}</p></div>)}
      </section>

      <section className="grid items-stretch gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <div className="wb-surface flex min-h-[620px] h-full flex-col p-4">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <div><h2 className="text-sm font-medium text-slate-900">动态实例关系</h2><p className="mt-1 text-[11px] text-slate-500">图中只保留已经到达的 Observation 和事实。</p></div>
            <div className="flex items-center gap-3 text-[10px] text-slate-400"><span className="inline-flex items-center gap-1"><span className="h-2 w-2 rounded-full bg-blue-600" />最新事件</span><span className="inline-flex items-center gap-1"><span className="h-px w-4 border-t border-dashed border-slate-400" />失效关系</span></div>
          </div>
          <div className="mt-3 flex min-h-0 flex-1 items-stretch">{graph?.nodes?.length ? <LiveGraph graph={graph} latestEvent={run.latest_event} onNode={(node) => { setSelectedNode(node); setSelectedEdge(null); }} onEdge={(edge) => { setSelectedEdge(edge); setSelectedNode(null); }} /> : <div className="grid min-h-[520px] flex-1 place-items-center rounded-lg bg-slate-50 text-xs text-slate-400">等待第一条数据</div>}</div>
        </div>

        <aside className="flex h-full flex-col gap-4">
          <section className="wb-surface p-4"><div className="flex items-center justify-between"><h2 className="text-sm font-medium">最新观测</h2>{run.latest_event && <span className="text-[10px] text-slate-400">{formatValue(run.last_received_at)}</span>}</div>{run.latest_event ? <div className="mt-3 space-y-2 text-[11px]"><p className="break-all font-mono text-slate-600">{run.latest_event.event_key}</p><p className="text-slate-500">episode：{formatValue(run.latest_event.episode_id)} · Ordinal：{formatValue(run.latest_event.ordinal)}</p><KeyValueTable values={latestPayload} /></div> : <p className="mt-3 text-xs text-slate-400">尚未接收数据</p>}</section>
          <section className="wb-surface p-4"><h2 className="text-sm font-medium">当前状态</h2><div className="mt-3 space-y-2">{stateRows.map(([key, value]) => { const item = value && typeof value === "object" ? value as Record<string, unknown> : null; return <div key={key} className="rounded border border-slate-100 bg-slate-50 p-2 text-[11px]"><span className="text-slate-500">{key}</span><p className="mt-1 break-words text-slate-800">{item ? formatValue(item.value || item.object_id) : formatValue(value)}</p></div>; })}{!stateRows.length && <p className="text-xs text-slate-400">暂无状态</p>}</div></section>
          {selectedNode && <section className="wb-surface p-4"><h2 className="text-sm font-medium">选中实例</h2><p className="mt-2 break-all font-mono text-[10px] text-slate-500">{selectedNode.id}</p><p className="mt-1 text-[11px] text-slate-600">类型：{selectedNode.entity_type || "—"}</p><div className="mt-2"><KeyValueTable values={selectedNode.properties || {}} /></div></section>}
          {selectedEdge && <section className="wb-surface p-4"><h2 className="text-sm font-medium">关系检查器</h2><div className="mt-3 space-y-1 text-[11px] text-slate-600"><p>关系：{selectedEdge.type || selectedEdge.label || "—"}</p><p>起点：{selectedEdge.source}</p><p>终点：{selectedEdge.target}</p><p>有效区间：{formatValue(selectedEdge.properties?.valid_from_ordinal)} → {formatValue(selectedEdge.properties?.valid_to_ordinal)}</p><p>来源事件：{formatValue(selectedEdge.properties?.source_event_id)}</p><p>EvidenceRef：{formatValue(selectedEdge.properties?.evidence_ref_id)}</p></div></section>}
        </aside>
      </section>

      <section className="wb-surface overflow-hidden">
        <button onClick={() => setShowHistory((value) => !value)} className="flex w-full items-center justify-between px-4 py-3 text-left"><span className="flex items-center gap-2 text-sm font-medium"><History size={15} />历史记录</span>{showHistory ? <ChevronUp size={15} /> : <ChevronDown size={15} />}</button>
        {showHistory && <div className="border-t border-slate-200 px-4 py-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><p className="text-xs text-slate-600">只可回看已经接收的 Ordinal</p><p className="mt-1 text-[11px] text-slate-400">范围：{formatValue(first)} → {formatValue(latest)}</p></div><div className="flex items-center gap-2"><button onClick={() => { setShowHistory(false); setHistoryAt(Number(latest)); setHistoryMode("valid"); }} className={`rounded border px-2 py-1 text-[11px] ${historyAt === latest && historyMode === "valid" ? "border-slate-800 bg-slate-800 text-white" : "border-slate-300"}`}>返回实时</button><button onClick={() => setHistoryMode((value) => value === "valid" ? "all" : "valid")} className="rounded border border-slate-300 px-2 py-1 text-[11px]">{historyMode === "all" ? "显示有效关系" : "显示失效关系"}</button></div></div>{first !== null && latest !== null && Number(latest) >= Number(first) && <input aria-label="已接收历史范围" type="range" min={Number(first)} max={Number(latest)} step="any" value={historyAt ?? Number(latest)} onChange={(event) => setHistoryAt(Number(event.target.value))} className="mt-4 w-full accent-slate-800" />}<div className="mt-2 flex justify-between text-[11px] text-slate-400"><span>{formatValue(first)}</span><span>查看：{formatValue(historyAt ?? latest)}</span><span>{formatValue(latest)}</span></div></div>}
      </section>

      {run.status === "completed" && <section className="flex flex-wrap items-center justify-between gap-3 rounded-xl border border-emerald-200 bg-emerald-50 px-4 py-3"><div className="flex items-center gap-2"><CheckCircle2 size={16} className="text-emerald-700" /><div><p className="text-sm font-medium text-emerald-900">数据源已结束</p><p className="mt-1 text-xs text-emerald-800">可以发布当前运行的数据模型快照。</p></div></div><div className="flex items-center gap-2"><button onClick={restartDemo} className="rounded border border-emerald-300 bg-white px-3 py-2 text-xs text-emerald-800">重新演示</button><button onClick={() => void publish()} disabled={!!busy} className="rounded bg-emerald-700 px-3 py-2 text-xs text-white disabled:opacity-50">{busy === "publish" ? "发布中" : "发布快照"}</button></div></section>}
      {run.status === "published" && <section className="flex flex-wrap items-center justify-between gap-3 wb-surface px-4 py-3 text-xs text-slate-600"><span>快照已发布：<span className="font-mono">{run.published_snapshot_id || "—"}</span></span><button onClick={restartDemo} className="rounded border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700">重新演示</button></section>}
    </div>
  );
}
