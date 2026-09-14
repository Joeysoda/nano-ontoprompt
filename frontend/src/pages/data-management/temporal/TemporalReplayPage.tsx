import { useCallback, useEffect, useRef, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import cytoscape from "cytoscape";
import {
  ArrowLeft,
  CheckCircle2,
  CirclePause,
  FastForward,
  Loader2,
  Play,
  RefreshCw,
  SkipForward,
  Square,
  TriangleAlert,
} from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Replay = {
  id: string;
  replay_id: string;
  ontology_id: string;
  status: string;
  time_kind: string;
  series_ids: string[];
  start_time?: number;
  end_time?: number;
  current_time?: number;
  speed: number;
  current_batch_index: number;
  total_batches: number;
  source_rows: number;
  selected_rows: number;
  normalized_rows: number;
  metrics?: Record<string, any>;
  progress?: Record<string, any>;
  latest_rows?: Array<Record<string, any>>;
  latest_batch?: Record<string, any>;
  error?: string;
};

type Graph = { nodes: any[]; edges: any[]; total_instances?: number; total_edges?: number; next_offset?: number | null; available?: boolean };

const fmt = (value: any) => value === null || value === undefined || value === "" ? "—" : String(value);
const active = new Set(["created", "queued", "running", "pausing", "paused"]);
const errorText = (reason: any, fallback: string) => {
  const detail = reason?.response?.data?.detail ?? reason?.detail;
  if (typeof detail === "string") return detail;
  if (detail && typeof detail === "object" && typeof detail.message === "string") return detail.message;
  if (typeof reason?.message === "string") return reason.message;
  return fallback;
};

export default function TemporalReplayPage() {
  const { replayId } = useParams<{ replayId: string }>();
  const navigate = useNavigate();
  const cyRef = useRef<cytoscape.Core | null>(null);
  const canvasRef = useRef<HTMLDivElement | null>(null);
  const graphRequestRef = useRef(0);
  const [replay, setReplay] = useState<Replay | null>(null);
  const [batches, setBatches] = useState<any[]>([]);
  const [graph, setGraph] = useState<Graph | null>(null);
  const [graphLimit, setGraphLimit] = useState("200");
  const [customGraphLimit, setCustomGraphLimit] = useState("800");
  const [loadingGraph, setLoadingGraph] = useState(false);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [speed, setSpeed] = useState(5);
  const [viewTime, setViewTime] = useState<number | null>(null);
  const [followLatest, setFollowLatest] = useState(true);
  const [segmentStart, setSegmentStart] = useState("");
  const [segmentEnd, setSegmentEnd] = useState("");
  const [busy, setBusy] = useState("");
  const [error, setError] = useState("");

  const loadGraph = useCallback(async () => {
    if (!replayId) return;
    const requestId = ++graphRequestRef.current;
    setLoadingGraph(true);
    const desired = graphLimit === "all"
      ? Number.POSITIVE_INFINITY
      : graphLimit === "custom"
        ? Math.max(1, Math.min(100000, Number(customGraphLimit) || 800))
        : Math.max(1, Number(graphLimit));
    const requested = Math.min(500, desired === Number.POSITIVE_INFINITY ? 500 : desired);
    let offset = 0;
    const pages: any[] = [];
    const edges: any[] = [];
    let last: Graph | null = null;
    try {
      // The API caps a single response at 500. “全部” follows the cursor so
      // the user can see the full committed graph; a newer request cancels
      // this loop logically before the next page is applied.
      do {
        if (requestId !== graphRequestRef.current) return;
        const page = await apiClientV2.get<Graph>(`/temporal/replays/${replayId}/graph`, { params: { offset, limit: requested, at: viewTime ?? undefined } });
        if (requestId !== graphRequestRef.current) return;
        last = page;
        pages.push(...(page.nodes || []));
        edges.push(...(page.edges || []));
        offset = page.next_offset ?? -1;
        if (desired !== Number.POSITIVE_INFINITY && pages.length >= desired) break;
      } while (offset >= 0);
      if (!last) return;
      const uniqueEdges = Array.from(new Map(edges.map((edge: any) => [edge.id, edge])).values());
      setGraph({ ...last, nodes: pages, edges: uniqueEdges });
    } finally {
      if (requestId === graphRequestRef.current) setLoadingGraph(false);
    }
  }, [customGraphLimit, graphLimit, replayId, viewTime]);

  const cancelGraphLoad = () => {
    graphRequestRef.current += 1;
    setLoadingGraph(false);
  };

  const load = useCallback(async () => {
    if (!replayId) return;
    try {
      setError("");
      const [r, bs] = await Promise.all([
        apiClientV2.get<Replay>(`/temporal/replays/${replayId}`),
        apiClientV2.get<any>(`/temporal/replays/${replayId}/batches`),
      ]);
      setReplay(r);
      setSpeed(Number(r.speed || 5));
      if (followLatest && r.current_time !== null && r.current_time !== undefined) {
        setViewTime(Number(r.current_time));
      }
      setBatches(bs?.batches || []);
      await loadGraph();
    } catch (reason: any) {
      setError(errorText(reason, "无法读取时序模拟"));
    }
  }, [loadGraph, replayId]);

  useEffect(() => { load(); }, [load]);
  useEffect(() => {
    if (!replay || !active.has(replay.status)) return;
    const timer = window.setInterval(load, 900);
    return () => window.clearInterval(timer);
  }, [load, replay?.status]);

  useEffect(() => {
    if (!canvasRef.current || !graph) return;
    cyRef.current?.destroy();
    const cy = cytoscape({
      container: canvasRef.current,
      elements: [
        ...(graph.nodes || []).map((node: any) => ({ data: { id: node.id, label: node.entity_type, node } })),
        ...(graph.edges || []).map((edge: any) => ({ data: { id: edge.id, source: edge.source, target: edge.target, label: edge.type } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "background-color": "#334155", color: "#0f172a", "font-size": 10, "text-valign": "bottom", "text-margin-y": 5, width: 18, height: 18 } },
        { selector: "edge", style: { width: 1.2, "line-color": "#cbd5e1", "target-arrow-color": "#94a3b8", "target-arrow-shape": "triangle", label: "data(label)", "font-size": 8, color: "#64748b", "curve-style": "bezier" } },
        { selector: ".selected", style: { "background-color": "#2563eb", "line-color": "#2563eb", "target-arrow-color": "#2563eb", "border-width": 3, "border-color": "#bfdbfe" } },
      ],
      layout: { name: "cose", animate: false, fit: true, padding: 24 },
    });
    cy.on("tap", "node", (event) => {
      cy.nodes().removeClass("selected");
      event.target.addClass("selected");
      setSelectedNode(event.target.data("node"));
    });
    cyRef.current = cy;
    return () => { cy.destroy(); cyRef.current = null; };
  }, [graph]);

  const control = async (action: string) => {
    if (!replayId) return;
    setBusy(action); setError("");
    try {
      await apiClientV2.post(`/temporal/replays/${replayId}/control`, { action, speed });
      await load();
    } catch (reason: any) {
      setError(errorText(reason, "控制请求失败"));
    } finally { setBusy(""); }
  };

  const appendSegment = async () => {
    if (!replayId || !segmentStart || !segmentEnd) return;
    setBusy("append"); setError("");
    try {
      await apiClientV2.post(`/temporal/replays/${replayId}/segments`, {
        start_time: Number(segmentStart),
        end_time: Number(segmentEnd),
      });
      setFollowLatest(true);
      setSegmentStart(""); setSegmentEnd("");
      await load();
    } catch (reason: any) {
      setError(errorText(reason, "追加时间段失败"));
    } finally { setBusy(""); }
  };

  if (!replay && !error) return <div className="flex min-h-56 items-center gap-2 text-sm text-slate-500"><Loader2 className="h-4 w-4 animate-spin" /> 读取时序模拟</div>;
  if (!replay) return <div className="wb-alert wb-alert-danger m-6"><TriangleAlert size={16} /> {error}</div>;
  const metrics = replay.metrics || {};
  const percent = Number(replay.progress?.pct || 0);
  const columns = Array.from(new Set((replay.latest_rows || []).flatMap((row) => Object.keys(row)))).slice(0, 10);
  const statusLabel: Record<string, string> = { created: "已创建", queued: "排队中", running: "运行中", pausing: "即将暂停", paused: "已暂停", completed: "已完成", failed: "失败", cancelled: "已取消" };

  return (
    <div className="mx-auto max-w-[1500px] space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <button onClick={() => navigate("/data/temporal")} className="mb-2 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900"><ArrowLeft size={13} /> 返回时序数据</button>
          <h2 className="text-2xl font-semibold text-slate-900">FactoryNet 时序数据模拟</h2>
          <p className="mt-1 text-sm text-slate-500">数据按 time_s / event_seq 分批到达，图谱只显示已提交批次 · episode：{replay.series_ids.join(", ")}</p>
        </div>
        <button onClick={load} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"><RefreshCw size={14} /> 刷新</button>
      </div>
      {error && <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700"><TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" /> {error}</div>}
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">{replay.status === "completed" ? <CheckCircle2 className="h-5 w-5 text-emerald-600" /> : replay.status === "failed" ? <TriangleAlert className="h-5 w-5 text-red-600" /> : <Loader2 className="h-5 w-5 animate-spin text-blue-600" />}<div><p className="font-medium text-slate-900">{statusLabel[replay.status] || replay.status}</p><p className="mt-0.5 text-sm text-slate-500">{replay.error || `批次 ${Math.max(0, replay.current_batch_index + 1)} / ${replay.total_batches}`}</p></div></div>
          <div className="flex items-center gap-2"><label className="text-xs text-slate-500">速度<select value={speed} onChange={(e) => setSpeed(Number(e.target.value))} className="ml-1 rounded border px-2 py-1"><option value={1}>1×</option><option value={5}>5×</option><option value={10}>10×</option><option value={25}>25×</option><option value={50}>50×</option></select></label><span className="text-sm font-medium tabular-nums text-slate-700">{percent.toFixed(0)}%</span></div>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100"><div className={`h-full rounded-full transition-all ${replay.status === "failed" ? "bg-red-500" : "bg-blue-600"}`} style={{ width: `${Math.min(100, Math.max(0, percent))}%` }} /></div>
        <div className="mt-3 flex flex-wrap gap-2"><button disabled={!!busy || !["created", "paused", "queued"].includes(replay.status)} onClick={() => control(replay.status === "paused" ? "resume" : "start")} className="wb-button-primary disabled:opacity-40"><Play size={14} /> {replay.status === "paused" ? "继续" : "开始"}</button><button disabled={!!busy || !["running", "pausing"].includes(replay.status)} onClick={() => control("pause")} className="wb-button-secondary disabled:opacity-40"><CirclePause size={14} /> 暂停</button><button disabled={!!busy || !["paused", "created"].includes(replay.status)} onClick={() => control("step")} className="wb-button-secondary disabled:opacity-40"><SkipForward size={14} /> 单步</button><button disabled={!!busy || ["completed", "failed", "cancelled"].includes(replay.status)} onClick={() => control("cancel")} className="wb-button-secondary text-red-700 disabled:opacity-40"><Square size={13} /> 取消</button><span className="ml-2 self-center text-xs text-slate-500">当前 time_s：<b>{fmt(replay.current_time)}</b> · Ordinal，不转换为日期</span></div>
      </section>
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-end justify-between gap-4">
          <div className="min-w-[280px] flex-1">
            <div className="flex items-center justify-between gap-3">
              <div><h3 className="font-medium">时间轴</h3><p className="mt-1 text-xs text-slate-500">拖动查看截至某个原始 time_s 的已提交图谱；不会修改模拟进度。</p></div>
              <span className="text-sm tabular-nums text-slate-700">{fmt(viewTime ?? replay.start_time)} s</span>
            </div>
            <input
              type="range"
              min={Number(replay.start_time ?? 0)}
              max={Number(replay.end_time ?? replay.start_time ?? 0)}
              step="any"
              value={viewTime ?? Number(replay.start_time ?? 0)}
              onChange={(event) => { setFollowLatest(false); setViewTime(Number(event.target.value)); }}
              disabled={replay.end_time === undefined || replay.end_time === null || replay.end_time <= replay.start_time!}
              className="mt-3 w-full accent-blue-600 disabled:opacity-40"
            />
            <div className="mt-1 flex justify-between text-[11px] text-slate-400"><span>{fmt(replay.start_time)} s</span><span>{fmt(replay.end_time)} s</span></div>
          </div>
          <button type="button" onClick={() => { setFollowLatest(true); setViewTime(replay.current_time ?? replay.end_time ?? replay.start_time ?? null); }} className={`rounded-lg border px-3 py-2 text-xs ${followLatest ? "border-blue-300 bg-blue-50 text-blue-700" : "border-slate-300 bg-white text-slate-600"}`}>跟随最新批次</button>
        </div>
      </section>
      <section className="grid grid-cols-2 gap-3 md:grid-cols-4 xl:grid-cols-8">{[["源记录", replay.source_rows], ["选中记录", replay.selected_rows], ["已规范化", replay.normalized_rows], ["已提交批次", metrics.committed_batches || 0], ["节点写入", metrics.nodes_written || 0], ["关系写入", metrics.edges_written || 0], ["问题", metrics.issues || 0], ["图中节点", graph?.total_instances || 0]].map(([label, value]) => <div key={String(label)} className="rounded-xl border border-slate-200 bg-white p-4"><p className="text-xs text-slate-500">{label}</p><p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">{fmt(value)}</p></div>)}</section>
      <section className="grid gap-5 xl:grid-cols-[390px_minmax(0,1fr)_310px]">
        <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex items-center justify-between"><h3 className="font-medium">最近到达的原始记录</h3><span className="text-xs text-slate-500">每批最多 20 行</span></div><div className="mt-3 max-h-[560px] overflow-auto rounded border"><table className="w-full text-[11px]"><thead className="sticky top-0 bg-slate-50"><tr>{columns.map((column) => <th key={column} className="whitespace-nowrap px-2 py-2 text-left">{column}</th>)}</tr></thead><tbody>{(replay.latest_rows || []).map((row, index) => <tr key={index} className="border-t">{columns.map((column) => <td key={column} className="max-w-[130px] truncate whitespace-nowrap px-2 py-2">{fmt(row[column])}</td>)}</tr>)}</tbody></table>{!(replay.latest_rows || []).length && <p className="p-4 text-xs text-slate-500">尚未提交批次</p>}</div></div>
        <div className="rounded-xl border border-slate-200 bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-3"><div><h3 className="font-medium">增长中的实例图</h3><p className="mt-1 text-xs text-slate-500">画布只渲染已提交数据；节点数量可调整，全部模式按 500 个一页加载。</p></div><div className="flex items-center gap-2"><select value={graphLimit} onChange={(e) => setGraphLimit(e.target.value)} className="rounded border px-2 py-1 text-xs"><option value="50">50 个</option><option value="100">100 个</option><option value="200">200 个</option><option value="500">500 个</option><option value="1000">1000 个</option><option value="custom">自定义</option><option value="all">全部（分页）</option></select>{graphLimit === "custom" && <input type="number" min="1" max="100000" value={customGraphLimit} onChange={(e) => setCustomGraphLimit(e.target.value)} className="w-20 rounded border px-2 py-1 text-xs" />}{loadingGraph && <button type="button" onClick={cancelGraphLoad} className="rounded border border-red-200 px-2 py-1 text-xs text-red-700">停止加载</button>}</div></div><div ref={canvasRef} className="mt-3 h-[520px] rounded-lg bg-slate-50" />{loadingGraph && <p className="mt-2 text-xs text-slate-500">正在加载图谱…</p>}{graph && !graph.available && <p className="mt-2 text-xs text-red-600">FalkorDB 当前不可用，已保留任务状态，启动后可刷新查看。</p>}</div>
        <div className="space-y-5"><div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="font-medium">选中节点</h3>{selectedNode ? <div className="mt-3 space-y-2 text-xs"><p className="break-all font-mono text-slate-600">{selectedNode.id}</p><p>类型：{selectedNode.entity_type}</p>{Object.entries(selectedNode.properties || {}).slice(0, 18).map(([key, value]) => <div key={key} className="flex justify-between gap-2 border-t pt-1"><span className="text-slate-500">{key}</span><span className="max-w-[170px] truncate text-right">{fmt(value)}</span></div>)}</div> : <p className="mt-3 text-xs text-slate-500">点击画布中的节点查看属性和 time_s。</p>}</div><div className="rounded-xl border border-slate-200 bg-white p-4"><h3 className="font-medium">批次时间轴</h3><div className="mt-3 max-h-80 space-y-1 overflow-auto">{batches.map((batch) => <div key={batch.id} className={`rounded border px-2 py-2 text-xs ${batch.status === "completed" ? "border-emerald-200 bg-emerald-50" : batch.status === "failed" ? "border-red-200 bg-red-50" : "border-slate-200"}`}><div className="flex justify-between"><span>#{batch.batch_no + 1}</span><span>{batch.status}</span></div><div className="mt-1 text-slate-500">{fmt(batch.time_from)} → {fmt(batch.time_to)} · {batch.normalized_rows || 0} 行</div></div>)}</div></div></div>
      </section>
      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div><h3 className="font-medium">继续追加时间段</h3><p className="mt-1 text-xs text-slate-500">从当前模拟末尾之后继续读取同一 FactoryNet 数据。开始值必须大于 {fmt(replay.end_time)} s。</p></div>
          <div className="flex flex-wrap items-end gap-2">
            <label className="text-xs text-slate-500">开始 time_s<input type="number" step="any" value={segmentStart} onChange={(event) => setSegmentStart(event.target.value)} placeholder={String((replay.end_time ?? 0) + 1)} className="mt-1 block w-28 rounded border px-2 py-1.5 text-sm text-slate-900" /></label>
            <label className="text-xs text-slate-500">结束 time_s<input type="number" step="any" value={segmentEnd} onChange={(event) => setSegmentEnd(event.target.value)} placeholder={String((replay.end_time ?? 0) + 10)} className="mt-1 block w-28 rounded border px-2 py-1.5 text-sm text-slate-900" /></label>
            <button type="button" disabled={!!busy || ["failed", "cancelled"].includes(replay.status) || !segmentStart || !segmentEnd} onClick={appendSegment} className="wb-button-secondary disabled:opacity-40">{busy === "append" ? "追加中..." : "加入模拟"}</button>
          </div>
        </div>
      </section>
      {replay.status === "completed" && <div className="rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-900">模拟完成。现在看到的图谱是按 FactoryNet 原始 time_s 逐批提交后的结果；需要查看本体结构和 Data Model，可打开本体页面。</div>}
      <div className="flex justify-end"><button onClick={() => navigate(`/ontologies/${replay.ontology_id}?tab=data_model`)} className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"><FastForward size={14} /> 打开本体数据模型</button></div>
    </div>
  );
}
