import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useSearchParams } from "react-router-dom";
import { useNavigate } from "react-router-dom";
import cytoscape from "cytoscape";
import {
  CircleAlert,
  Database,
  Filter,
  Loader2,
  Pause,
  Play,
  RefreshCw,
  RotateCcw,
  Search,
  X,
} from "lucide-react";
import { apiClientV2 } from "@/api/client";
import {
  MultimodalEvidenceWorkspace,
  type MultimodalEvidence,
} from "@/pages/data-management/multimodal/MultimodalEvidenceWorkspace";

type DataClass = "regular" | "temporal" | "multimodal";
type PageSize = 50 | 100 | 200 | 500 | "all";

type TypeGroup = {
  id: string;
  name?: string;
  name_cn?: string;
  name_en?: string;
  description?: string;
  filter?: string;
  property_count?: number;
  relationship_count?: number;
  instance_count?: number;
  evidence_count?: number;
};

type InstanceNode = {
  id: string;
  labels?: string[];
  entity_type?: string;
  node_kind?: string;
  event_seq?: number | string | null;
  event_time?: string | null;
  properties?: Record<string, unknown>;
  evidence_count?: number;
  evidence?: Array<Record<string, unknown>>;
};

type InstanceEdge = {
  id: string;
  source: string;
  target: string;
  type?: string;
  label?: string;
  edge_kind?: string;
  properties?: Record<string, unknown>;
  evidence_count?: number;
  evidence?: Array<Record<string, unknown>>;
};

type DataModelResponse = {
  ontology_id: string;
  data_class: DataClass;
  type_groups?: TypeGroup[];
  nodes: InstanceNode[];
  edges: InstanceEdge[];
  total_nodes?: number;
  total_edges?: number;
  pagination?: {
    offset?: number;
    limit?: number;
    returned?: number;
    total?: number;
    next_offset?: number | null;
  };
  time?: {
    kind?: string;
    mode?: "cumulative" | "window";
    current?: string | null;
    min?: string | null;
    max?: string | null;
    dates?: string[];
    buckets?: Array<{ timestamp: string; count: number }>;
    episodes?: string[];
    episode_id?: string | null;
  } | null;
  multimodal?: { sample_count?: number; asset_count?: number };
  available?: boolean;
  graph_backend?: string;
  error?: string;
};

const colors = [
  "#155e75",
  "#166534",
  "#92400e",
  "#6b21a8",
  "#9f1239",
  "#1d4ed8",
  "#0f766e",
];

function stableColor(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1)
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  return colors[Math.abs(hash) % colors.length];
}

function stringify(value: unknown, max = 180) {
  if (value === null || value === undefined || value === "") return "—";
  const text =
    typeof value === "string" || typeof value === "number" || typeof value === "boolean"
      ? String(value)
      : JSON.stringify(value);
  return text.length > max ? `${text.slice(0, max)}…` : text;
}

function nodeLabel(node: InstanceNode) {
  const props = node.properties || {};
  const preferred = [
    props.name,
    props.label,
    props.sample_key,
    props.equipment_id,
    props.unit_id,
    props.episode_id,
    props.row_identity,
    node.id,
  ];
  return String(preferred.find((value) => value !== null && value !== undefined && value !== "") || node.id).slice(0, 28);
}

function displayType(node: InstanceNode) {
  return String(node.entity_type || node.labels?.[0] || node.node_kind || "实体");
}

function parsePageSize(value: string | null): PageSize {
  if (value === "all") return "all";
  const parsed = Number(value);
  return parsed === 50 || parsed === 100 || parsed === 200 || parsed === 500 ? parsed : 200;
}

function errorText(error: unknown) {
  const detail = error as {
    response?: { data?: { detail?: unknown } };
    detail?: unknown;
    message?: unknown;
  };
  const message = (
    (detail.response?.data?.detail as { message?: unknown } | undefined)?.message ||
    detail.response?.data?.detail ||
    detail.detail ||
    detail.message ||
    "数据模型加载失败"
  );
  return typeof message === "string" ? message : String(message);
}

function PropertyTable({ values }: { values: Record<string, unknown> }) {
  const entries = Object.entries(values).filter(([key]) => !key.startsWith("_"));
  if (!entries.length) return <p className="text-xs text-slate-400">暂无字段值</p>;
  return (
    <div className="overflow-hidden rounded-lg border border-slate-200">
      {entries.slice(0, 28).map(([key, value]) => (
        <div key={key} className="grid grid-cols-[minmax(90px,0.42fr)_minmax(0,1fr)] border-b border-slate-100 last:border-0">
          <span className="bg-slate-50 px-2.5 py-2 text-[11px] text-slate-500">{key}</span>
          <span className="break-words px-2.5 py-2 text-[11px] text-slate-700">{stringify(value)}</span>
        </div>
      ))}
    </div>
  );
}

export default function DataModelTab({
  ontologyId,
  dataClass,
}: {
  ontologyId: string;
  dataClass?: DataClass | string | null;
}) {
  const navigate = useNavigate();
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const requestSeqRef = useRef(0);
  const [searchParams, setSearchParams] = useSearchParams();
  const [data, setData] = useState<DataModelResponse | null>(null);
  const [loading, setLoading] = useState(true);
  const [loadingAll, setLoadingAll] = useState(false);
  const [allProgress, setAllProgress] = useState(0);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<
    | { kind: "node"; value: InstanceNode }
    | { kind: "edge"; value: InstanceEdge }
    | null
  >(null);
  const [query, setQuery] = useState("");
  const [playing, setPlaying] = useState(false);
  const [evidence, setEvidence] = useState<MultimodalEvidence | null>(null);
  const [evidenceLoading, setEvidenceLoading] = useState(false);

  const pageSize = parsePageSize(searchParams.get("limit"));
  const offset = Math.max(0, Number(searchParams.get("offset") || 0) || 0);
  const entityType = searchParams.get("entity_type") || "";
  const episodeId = searchParams.get("episode_id") || "";
  const at = searchParams.get("at");
  const mode = searchParams.get("mode") === "window" ? "window" : "cumulative";
  const actualClass: DataClass =
    data?.data_class || (dataClass === "temporal" || dataClass === "multimodal" ? dataClass : "regular");

  const setUrl = useCallback(
    (updates: Record<string, string | number | null | undefined>) => {
      const next = new URLSearchParams(searchParams);
      Object.entries(updates).forEach(([key, value]) => {
        if (value === null || value === undefined || value === "") next.delete(key);
        else next.set(key, String(value));
      });
      setSearchParams(next, { replace: true });
    },
    [searchParams, setSearchParams],
  );

  const requestPage = useCallback(
    async (requestOffset: number, requestLimit: number) => {
      const params: Record<string, string | number> = {
        limit: requestLimit,
        offset: requestOffset,
        mode,
      };
      if (entityType) params.entity_type = entityType;
      if (episodeId) params.episode_id = episodeId;
      if (at) params.at = at;
      return apiClientV2.get<DataModelResponse>(`/ontologies/${ontologyId}/data-model`, { params });
    },
    [at, entityType, episodeId, mode, ontologyId],
  );

  const load = useCallback(async () => {
    const requestId = ++requestSeqRef.current;
    setLoading(true);
    setError("");
    setSelected(null);
    setEvidence(null);
    try {
      if (pageSize === "all") {
        setLoadingAll(true);
        setAllProgress(0);
        const mergedNodes = new Map<string, InstanceNode>();
        const mergedEdges = new Map<string, InstanceEdge>();
        let requestOffset = 0;
        let first: DataModelResponse | null = null;
        let guard = 0;
        while (guard < 1000) {
          const page = await requestPage(requestOffset, 500);
          if (requestId !== requestSeqRef.current) return;
          if (!first) first = page;
          for (const node of page.nodes || []) mergedNodes.set(node.id, node);
          for (const edge of page.edges || []) mergedEdges.set(edge.id, edge);
          if (guard === 0 || guard % 2 === 1) {
            setData({
              ...page,
              nodes: [...mergedNodes.values()],
              edges: [...mergedEdges.values()],
              pagination: { ...page.pagination, offset: 0, limit: mergedNodes.size, returned: mergedNodes.size, next_offset: page.pagination?.next_offset },
            });
          }
          const total = Number(page.pagination?.total || page.total_nodes || 0);
          setAllProgress(total ? Math.min(100, Math.round((mergedNodes.size / total) * 100)) : 100);
          const next = page.pagination?.next_offset;
          if (next === null || next === undefined || next <= requestOffset) break;
          requestOffset = next;
          guard += 1;
        }
        if (!first) throw new Error("没有返回数据");
        setData({
          ...first,
          nodes: [...mergedNodes.values()],
          edges: [...mergedEdges.values()],
          pagination: { ...first.pagination, offset: 0, limit: mergedNodes.size, returned: mergedNodes.size, next_offset: null },
        });
        if (!at && first.time?.current) setUrl({ at: first.time.current });
      } else {
        const page = await requestPage(offset, pageSize);
        if (requestId !== requestSeqRef.current) return;
        setData(page);
        if (page.error) setError(page.error);
        if (!at && page.time?.current) setUrl({ at: page.time.current });
      }
    } catch (reason) {
      if (requestId === requestSeqRef.current) {
        setData(null);
        setError(errorText(reason));
      }
    } finally {
      if (requestId === requestSeqRef.current) {
        setLoading(false);
        setLoadingAll(false);
      }
    }
  }, [at, offset, pageSize, requestPage, setUrl]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => {
      window.clearTimeout(timer);
      requestSeqRef.current += 1;
    };
  }, [load]);

  const selectedNode = selected?.kind === "node" ? selected.value : null;
  const selectedEdge = selected?.kind === "edge" ? selected.value : null;
  const nodeById = useMemo(
    () => new Map((data?.nodes || []).map((node) => [node.id, node])),
    [data],
  );
  const relatedEdges = useMemo(
    () =>
      selectedNode
        ? (data?.edges || []).filter((edge) => edge.source === selectedNode.id || edge.target === selectedNode.id)
        : [],
    [data, selectedNode],
  );
  const hitIds = useMemo(() => {
    const term = query.trim().toLocaleLowerCase();
    if (!term) return new Set<string>();
    const ids = new Set<string>();
    for (const node of data?.nodes || []) {
      const haystack = [node.id, node.entity_type, ...Object.entries(node.properties || {}).flatMap(([key, value]) => [key, stringify(value)])]
        .join(" ")
        .toLocaleLowerCase();
      if (haystack.includes(term)) ids.add(node.id);
    }
    for (const edge of data?.edges || []) {
      const haystack = [edge.id, edge.type, edge.label, stringify(edge.properties)].join(" ").toLocaleLowerCase();
      if (haystack.includes(term)) {
        ids.add(edge.id);
        ids.add(edge.source);
        ids.add(edge.target);
      }
    }
    return ids;
  }, [data, query]);

  useEffect(() => {
    if (!selectedNode || actualClass !== "multimodal") {
      window.setTimeout(() => setEvidence(null), 0);
      return;
    }
    const props = selectedNode.properties || {};
    const sampleId = String(props.sample_id || props.source_sample_id || "");
    if (!sampleId) {
      window.setTimeout(() => setEvidence(null), 0);
      return;
    }
    let cancelled = false;
    window.setTimeout(() => setEvidenceLoading(true), 0);
    apiClientV2
      .get<MultimodalEvidence>(`/multimodal/samples/${sampleId}/evidence`)
      .then((result) => {
        if (!cancelled) setEvidence(result);
      })
      .catch(() => {
        if (!cancelled) setEvidence(null);
      })
      .finally(() => {
        if (!cancelled) setEvidenceLoading(false);
      });
    return () => {
      cancelled = true;
    };
  }, [actualClass, selectedNode]);

  useEffect(() => {
    if (!data || !containerRef.current) return;
    // Cytoscape is a canvas renderer rather than a virtual list.  Loading all
    // pages is useful for counts/search, but laying out thousands of nodes at
    // once makes a browser unresponsive.  Keep the complete data in state and
    // use a bounded canvas projection for the visual surface.
    const renderNodes = data.nodes.length > 800 ? data.nodes.slice(0, 800) : data.nodes;
    const visibleIds = new Set(renderNodes.map((node) => node.id));
    const elements = [
      ...renderNodes.map((node) => ({
        data: {
          id: node.id,
          label: nodeLabel(node),
          color: stableColor(displayType(node)),
          raw: node,
          nodeKind: node.node_kind || "instance",
        },
      })),
        ...(data.edges || [])
        .filter((edge) => visibleIds.has(edge.source) && visibleIds.has(edge.target))
        .map((edge) => ({
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: edge.label || edge.type || "关联",
            raw: edge,
          },
        })),
    ];
    cyRef.current?.destroy();
    const cy = cytoscape({
      container: containerRef.current,
      elements,
      style: [
        {
          selector: "node",
          style: {
            label: "data(label)",
            "background-color": "data(color)",
            color: "#ffffff",
            "font-size": "10px",
            "font-weight": "bold",
            "text-valign": "center",
            "text-halign": "center",
            width: "76px",
            height: "44px",
            shape: "round-rectangle",
            "text-wrap": "wrap",
            "text-max-width": "62px",
            "text-outline-width": 2,
            "text-outline-color": "data(color)",
            "border-width": 1,
            "border-color": "#ffffff",
          },
        },
        {
          selector: 'node[nodeKind = "sample"]',
          style: { shape: "ellipse", width: "82px", height: "52px" },
        },
        {
          selector: 'node[nodeKind = "media"]',
          style: { shape: "rectangle", "border-style": "dashed" },
        },
        {
          selector: "edge",
          style: {
            label: "data(label)",
            "font-size": "8px",
            color: "#475569",
            width: 1.4,
            "line-color": "#94a3b8",
            "target-arrow-color": "#94a3b8",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.94,
            "text-background-padding": "2px",
            "text-rotation": "autorotate",
          },
        },
        { selector: ".active", style: { "border-width": 3, "border-color": "#f8fafc", width: "88px", height: "56px" } },
        { selector: ".muted", style: { opacity: 0.14 } },
        { selector: ".search-hit", style: { "border-width": 3, "border-color": "#f59e0b", "line-color": "#f59e0b", "target-arrow-color": "#f59e0b" } },
      ],
      layout: {
        ...(data.nodes.length > 800
          ? { name: "grid", avoidOverlap: true, condense: true, rows: Math.ceil(Math.sqrt(renderNodes.length)) }
          : {
              name: "cose",
              animate: false,
              randomize: true,
              componentSpacing: 80,
              idealEdgeLength: data.nodes.length > 150 ? 105 : 145,
              nodeRepulsion: data.nodes.length > 150 ? 3500 : 6500,
              numIter: data.nodes.length > 150 ? 420 : 760,
            }),
      } as cytoscape.LayoutOptions,
    });
    cy.on("tap", "node", (event) => setSelected({ kind: "node", value: event.target.data("raw") as InstanceNode }));
    cy.on("tap", "edge", (event) => setSelected({ kind: "edge", value: event.target.data("raw") as InstanceEdge }));
    cy.on("tap", (event) => {
      if (event.target === cy) setSelected(null);
    });
    cyRef.current = cy;
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [data]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("active muted search-hit");
    if (selectedNode) {
      const focus = cy.getElementById(selectedNode.id);
      focus.addClass("active");
      focus.neighborhood().addClass("active");
      cy.elements().not(".active").addClass("muted");
    } else if (selectedEdge) {
      const focus = cy.getElementById(selectedEdge.id);
      focus.addClass("active");
      focus.connectedNodes().addClass("active");
      cy.elements().not(".active").addClass("muted");
    }
    if (hitIds.size) {
      let hits = cy.collection();
      hitIds.forEach((id) => {
        const element = cy.getElementById(id);
        if (element.length) hits = hits.merge(element);
      });
      hits.addClass("search-hit");
      cy.elements().not(hits).addClass("muted");
    }
  }, [hitIds, selectedEdge, selectedNode]);

  const dates = useMemo(() => data?.time?.dates || [], [data?.time?.dates]);
  const currentAt = at || data?.time?.current || dates[dates.length - 1] || "";
  const currentIndex = Math.max(0, dates.findIndex((value) => String(value) === String(currentAt)));
  useEffect(() => {
    if (!playing || actualClass !== "temporal" || dates.length < 2) return;
    const timer = window.setInterval(() => {
      const index = dates.findIndex((value) => String(value) === String(currentAt));
      if (index >= dates.length - 1) {
        setPlaying(false);
        return;
      }
      setUrl({ at: dates[index + 1] });
    }, 900);
    return () => window.clearInterval(timer);
  }, [actualClass, currentAt, dates, playing, setUrl]);

  const pageTotal = Number(data?.pagination?.total ?? data?.total_nodes ?? data?.nodes?.length ?? 0);
  const nextOffset = data?.pagination?.next_offset;
  const hasPrevious = offset > 0;
  const hasNext = nextOffset !== null && nextOffset !== undefined;
  const changePageSize = (value: PageSize) => setUrl({ limit: value, offset: 0 });
  const changeType = (value: string) => setUrl({ entity_type: value || null, offset: 0 });
  const changeEpisode = (value: string) => setUrl({ episode_id: value || null, offset: 0, at: null });
  const changeAt = (value: string) => setUrl({ at: value || null, offset: 0 });

  const selectedNodeTitle = selectedNode ? nodeLabel(selectedNode) : "";
  const evidenceRoles = ["rgb", "depth", "mask", "point_cloud", "metadata"];

  if (loading && !data)
    return (
      <div className="flex min-h-[520px] items-center justify-center text-sm text-slate-500">
        <Loader2 size={17} className="mr-2 animate-spin" />
        正在加载数据模型
      </div>
    );
  if (error && !data)
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">
        <div className="flex items-center gap-2 font-medium"><CircleAlert size={16} />{error}</div>
        <button onClick={() => void load()} className="mt-3 rounded border border-red-200 bg-white px-3 py-1.5 text-xs">重试</button>
      </div>
    );

  return (
    <div className="space-y-4" data-testid="data-model-tab">
      <section className="rounded-xl border border-slate-200 bg-white p-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="flex items-center gap-2">
              <Database size={17} className="text-slate-700" />
              <h2 className="text-sm font-semibold text-slate-900">数据模型</h2>
              <span className="rounded bg-slate-100 px-2 py-0.5 text-[10px] text-slate-600">
                {actualClass === "temporal" ? "时序" : actualClass === "multimodal" ? "多模态" : "常规"}
              </span>
            </div>
            <p className="mt-1 text-xs text-slate-500">真实数据实例、实例关系与来源证据</p>
          </div>
          <button onClick={() => void load()} className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50" title="刷新数据模型">
            <RefreshCw size={15} className={loading ? "animate-spin" : ""} />
          </button>
        </div>
        {error && <div className="mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">{error}</div>}
        <div className="mt-4 flex flex-wrap items-center gap-2">
          <span className="mr-1 inline-flex items-center gap-1 text-xs text-slate-500"><Filter size={13} />实体类型</span>
          <button onClick={() => changeType("")} className={`rounded-full border px-3 py-1 text-xs ${!entityType ? "border-slate-800 bg-slate-800 text-white" : "border-slate-200 text-slate-600 hover:border-slate-400"}`}>全部</button>
          {(data?.type_groups || []).map((group) => {
            const value = group.filter || group.name_en || group.name || group.id;
            return <button key={`${group.id}-${value}`} onClick={() => changeType(value === entityType ? "" : String(value))} className={`rounded-full border px-3 py-1 text-xs ${entityType === value ? "border-slate-800 bg-slate-800 text-white" : "border-slate-200 text-slate-600 hover:border-slate-400"}`}>{group.name || group.name_cn || group.name_en || value}<span className="ml-1 opacity-70">{group.instance_count ?? 0}</span></button>;
          })}
        </div>
        <div className="mt-4 grid grid-cols-2 gap-2 md:grid-cols-4">
          {[
            ["类型", data?.type_groups?.length || 0],
            ["当前实例", data?.nodes?.length || 0],
            ["实例总数", pageTotal],
            ["关系总数", data?.total_edges ?? data?.edges?.length ?? 0],
          ].map(([label, value]) => <div key={String(label)} className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"><span className="block text-[11px] text-slate-500">{label}</span><strong className="mt-1 block text-base tabular-nums text-slate-800">{value}</strong></div>)}
        </div>
      </section>

      {actualClass === "temporal" && (
        <section className="rounded-xl border border-slate-200 bg-white px-4 py-3" data-testid="temporal-controls">
          <div className="flex flex-wrap items-center gap-3">
            <div className="min-w-[210px] flex-1">
              <div className="flex items-center justify-between text-xs text-slate-500"><span>Ordinal 时间位置</span><strong className="font-mono text-slate-800">{currentAt || "—"}</strong></div>
              <input aria-label="Ordinal 时间位置" type="range" min={0} max={Math.max(0, dates.length - 1)} value={currentIndex} onChange={(event) => changeAt(dates[Number(event.target.value)] || "")} className="mt-2 w-full accent-slate-700" disabled={!dates.length} />
              <div className="flex justify-between text-[10px] text-slate-400"><span>{data?.time?.min || "—"}</span><span>{data?.time?.max || "—"}</span></div>
            </div>
            <select aria-label="episode_id 筛选" value={episodeId} onChange={(event) => changeEpisode(event.target.value)} className="rounded-lg border border-slate-300 bg-white px-2.5 py-2 text-xs text-slate-700"><option value="">全部 episode_id</option>{(data?.time?.episodes || []).map((episode) => <option key={episode} value={episode}>{episode}</option>)}</select>
            <div className="flex rounded-lg border border-slate-200 p-0.5"><button onClick={() => setUrl({ mode: "cumulative", offset: 0 })} className={`px-2.5 py-1.5 text-xs ${mode === "cumulative" ? "rounded-md bg-slate-800 text-white" : "text-slate-600"}`}>累计</button><button onClick={() => setUrl({ mode: "window", offset: 0 })} className={`px-2.5 py-1.5 text-xs ${mode === "window" ? "rounded-md bg-slate-800 text-white" : "text-slate-600"}`}>当前位置窗口</button></div>
            <div className="flex items-center gap-1"><button onClick={() => setPlaying((value) => !value)} disabled={dates.length < 2} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-2 text-xs text-slate-700 disabled:opacity-40">{playing ? <Pause size={13} /> : <Play size={13} />}{playing ? "暂停" : "播放"}</button><button onClick={() => changeAt(data?.time?.max || dates[dates.length - 1] || "")} className="inline-flex items-center gap-1 rounded-lg border border-slate-200 px-2.5 py-2 text-xs text-slate-700"><RotateCcw size={13} />最新</button></div>
          </div>
          {data?.time?.buckets?.length ? <div className="mt-2 text-[11px] text-slate-500">已加载 {data.time.buckets.length} 个 Ordinal 桶 · 当前模式 {mode === "window" ? "当前位置窗口" : "累计"}</div> : <div className="mt-2 text-[11px] text-slate-400">没有可用的 Ordinal 时间位置</div>}
        </section>
      )}

      <div className="grid min-h-[650px] gap-4 xl:grid-cols-[minmax(0,1fr)_360px]">
        <section className="relative overflow-hidden rounded-xl border border-slate-200 bg-white">
          <div className="flex flex-wrap items-center justify-between gap-2 border-b border-slate-200 px-4 py-3"><div><p className="text-sm font-semibold text-slate-900">实例关系画布</p><p className="mt-0.5 text-xs text-slate-500">节点按实体类型着色，关系带方向</p></div><div className="flex items-center gap-2 text-xs text-slate-500"><span>{loadingAll ? `分批加载 ${allProgress}%` : data?.nodes && data.nodes.length > 800 ? `画布展示 800 / ${data.nodes.length} 个节点` : `${data?.nodes?.length || 0} 个节点`}</span>{loading && <Loader2 size={13} className="animate-spin" />}</div></div>
          {!data?.nodes?.length ? <div className="flex h-[560px] flex-col items-center justify-center text-slate-400"><Database size={24} /><p className="mt-3 text-sm">当前筛选没有数据实例</p></div> : <div ref={containerRef} className="h-[590px] w-full" />}
          <div className="border-t border-slate-200 px-4 py-3"><div className="flex flex-wrap items-center justify-between gap-3"><div className="flex items-center gap-1.5"><span className="text-xs text-slate-500">加载节点</span>{([50, 100, 200, 500, "all"] as PageSize[]).map((value) => <button key={String(value)} onClick={() => changePageSize(value)} className={`rounded border px-2 py-1 text-xs ${pageSize === value ? "border-slate-800 bg-slate-800 text-white" : "border-slate-200 text-slate-600 hover:border-slate-400"}`}>{value === "all" ? "全部（分批）" : value}</button>)}</div>{pageSize !== "all" && <div className="flex items-center gap-2 text-xs text-slate-500"><button disabled={!hasPrevious || loading} onClick={() => setUrl({ offset: Math.max(0, offset - pageSize) })} className="rounded border border-slate-200 px-2 py-1 disabled:opacity-40">上一页</button><span>{pageTotal ? `${offset + 1}–${Math.min(offset + (data?.nodes?.length || 0), pageTotal)} / ${pageTotal}` : "0 / 0"}</span><button disabled={!hasNext || loading} onClick={() => setUrl({ offset: nextOffset })} className="rounded border border-slate-200 px-2 py-1 disabled:opacity-40">下一页</button></div>}</div></div>
        </section>
        <aside className="flex min-h-[650px] flex-col rounded-xl border border-slate-200 bg-white">
          <div className="border-b border-slate-200 px-4 py-3"><p className="text-sm font-semibold text-slate-900">数据检查器</p><p className="mt-0.5 text-xs text-slate-500">{selectedNode ? "当前实例" : selectedEdge ? "当前关系" : "数据模型概览"}</p></div>
          <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
            {!selected && <div className="space-y-4"><div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600"><p>当前页 {data?.nodes?.length || 0} 个实例，{data?.edges?.length || 0} 条关系</p><p className="mt-1">可通过类型、时间位置或 episode_id 缩小范围。</p></div><div><h3 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">实体类型分组</h3><div className="space-y-2">{(data?.type_groups || []).map((group) => <div key={group.id} className="flex items-center justify-between rounded-lg border border-slate-200 px-3 py-2 text-xs"><span className="flex items-center gap-2 text-slate-700"><span className="h-2.5 w-2.5 rounded-full" style={{ background: stableColor(group.filter || group.name_en || group.name || group.id) }} />{group.name || group.name_cn || group.name_en || group.id}</span><span className="text-slate-500">{group.instance_count ?? 0}</span></div>)}</div></div></div>}
            {selectedNode && <div className="space-y-4"><div><div className="flex items-start justify-between gap-2"><div><h3 className="text-base font-semibold text-slate-900">{selectedNodeTitle}</h3><p className="mt-1 text-xs text-slate-500">{displayType(selectedNode)} · {selectedNode.node_kind === "sample" ? "样例" : selectedNode.node_kind === "media" ? "媒体资产" : "真实实例"}</p></div><div className="flex items-center gap-2">{actualClass === "temporal" && displayType(selectedNode).toLowerCase().includes("observation") && <button onClick={() => navigate(`/ontologies/${ontologyId}?tab=what_if&target_instance_id=${encodeURIComponent(selectedNode.id)}${episodeId ? `&episode_id=${encodeURIComponent(episodeId)}` : ""}${at ? `&at=${encodeURIComponent(at)}` : ""}&mode=${mode}`)} className="rounded-md bg-slate-800 px-2.5 py-1.5 text-[11px] text-white">用于 What-If</button>}<button onClick={() => setSelected(null)} className="text-slate-400 hover:text-slate-700"><X size={15} /></button></div></div><div className="mt-3 flex flex-wrap gap-2 text-[11px]"><span className="rounded bg-slate-100 px-2 py-1 text-slate-600">ID {selectedNode.id}</span>{selectedNode.event_seq !== null && selectedNode.event_seq !== undefined && <span className="rounded bg-slate-100 px-2 py-1 font-mono text-slate-600">Ordinal {selectedNode.event_seq}</span>}<span className="rounded bg-slate-100 px-2 py-1 text-slate-600">证据 {selectedNode.evidence_count || selectedNode.evidence?.length || 0}</span></div></div><section><h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">字段值</h4><PropertyTable values={selectedNode.properties || {}} /></section><section><h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">关联关系</h4>{relatedEdges.length ? <div className="space-y-2">{relatedEdges.slice(0, 20).map((edge) => <button key={edge.id} onClick={() => setSelected({ kind: "edge", value: edge })} className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-xs hover:border-slate-400"><strong className="block text-slate-800">{edge.label || edge.type || "关联"}</strong><span className="mt-1 block text-slate-500">{edge.source === selectedNode.id ? "指向" : "来自"} {nodeById.get(edge.source === selectedNode.id ? edge.target : edge.source) ? nodeLabel(nodeById.get(edge.source === selectedNode.id ? edge.target : edge.source)!) : "未加载节点"}</span></button>)}</div> : <p className="text-xs text-slate-400">当前页没有关联关系</p>}</section>{actualClass === "multimodal" && String(selectedNode.properties?.sample_id || selectedNode.properties?.source_sample_id || "") && <section><h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">模态证据</h4>{evidenceLoading ? <div className="flex items-center gap-2 text-xs text-slate-500"><Loader2 size={13} className="animate-spin" />读取样例证据</div> : evidence ? <><div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600"><p>样例：{evidence.sample.sample_key || evidence.sample.id}</p><p className="mt-1">场景：{evidence.sample.scene_id || "—"} · 标签：{evidence.sample.label || "—"}</p><p className="mt-1">资产：{evidence.assets.length} 个 · 完整度：{evidence.completeness?.missing_required_roles?.length ? "有缺失" : "齐全"}</p></div><MultimodalEvidenceWorkspace evidence={evidence} selectedRoles={evidenceRoles} /></> : <p className="text-xs text-slate-400">暂时无法读取样例证据</p>}</section>}</div>}
            {selectedEdge && <div className="space-y-4"><div className="flex items-start justify-between gap-2"><div><h3 className="text-base font-semibold text-slate-900">{selectedEdge.label || selectedEdge.type || "关联"}</h3><p className="mt-1 text-xs text-slate-500">{selectedEdge.edge_kind === "evidence" ? "证据关联" : "实例关系"}</p></div><button onClick={() => setSelected(null)} className="text-slate-400 hover:text-slate-700"><X size={15} /></button></div><div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600"><p>起点：{nodeById.get(selectedEdge.source) ? nodeLabel(nodeById.get(selectedEdge.source)!) : selectedEdge.source}</p><p className="mt-1">终点：{nodeById.get(selectedEdge.target) ? nodeLabel(nodeById.get(selectedEdge.target)!) : selectedEdge.target}</p><p className="mt-1">关系证据：{selectedEdge.evidence_count ?? selectedEdge.evidence?.length ?? 0} 条</p></div><section><h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">关系属性</h4><PropertyTable values={selectedEdge.properties || {}} /></section><section><h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">来源证据</h4>{selectedEdge.evidence?.length ? <div className="space-y-2">{selectedEdge.evidence.map((item, index) => <div key={`${String(item.id || "evidence")}-${index}`} className="rounded-lg border border-slate-200 px-3 py-2 text-[11px] text-slate-600"><p>{String(item.source_file || item.source_row_id || item.source_sample_id || item.source_media_id || "来源记录")}</p><p className="mt-1">{String(item.kind || "edge")} · {String(item.extractor || "规则")}{item.confidence != null ? ` · 置信度 ${Number(item.confidence).toFixed(2)}` : ""}</p>{item.evidence_text != null && <p className="mt-1 break-words text-slate-500">{String(item.evidence_text)}</p>}</div>)}</div> : <p className="text-xs text-slate-400">暂无关系级证据记录</p>}</section></div>}
            {selectedNode && <section className="mt-4"><h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">来源证据</h4>{selectedNode.evidence?.length ? <div className="space-y-2">{selectedNode.evidence.map((item, index) => <div key={`${String(item.id || "evidence")}-${index}`} className="rounded-lg border border-slate-200 px-3 py-2 text-[11px] text-slate-600"><p>{String(item.source_file || item.source_row_id || item.source_sample_id || item.source_media_id || "来源记录")}</p><p className="mt-1">{String(item.kind || "node")} · {String(item.extractor || "规则")}{item.confidence != null ? ` · 置信度 ${Number(item.confidence).toFixed(2)}` : ""}</p>{item.evidence_text != null && <p className="mt-1 break-words text-slate-500">{String(item.evidence_text)}</p>}</div>)}</div> : <p className="text-xs text-slate-400">暂无实例证据记录</p>}</section>}
          </div>
          <div className="border-t border-slate-200 p-3"><div className="relative"><Search size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400" /><input value={query} onChange={(event) => setQuery(event.target.value)} placeholder="搜索当前数据模型" className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-8 text-sm outline-none focus:border-slate-700" />{query && <button onClick={() => setQuery("")} className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400"><X size={15} /></button>}</div>{query && <p className="mt-2 text-xs text-slate-500">命中 {hitIds.size} 个节点或关系</p>}</div>
        </aside>
      </div>
    </div>
  );
}
