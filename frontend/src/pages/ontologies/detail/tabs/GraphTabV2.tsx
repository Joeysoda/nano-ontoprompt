import { useEffect, useMemo, useRef, useState } from "react";
import { useQuery } from '@tanstack/react-query';
import { useSearchParams } from "react-router-dom";
import cytoscape from "cytoscape";
import {
  CircleAlert,
  Database,
  Loader2,
  RefreshCw,
  Search,
  X,
} from "lucide-react";
import { apiClientV2 } from "@/api/client";
import { useResizableHeight } from "@/hooks/useResizableHeight";
import { deterministicGraphPositions } from "@/lib/relationshipGraph";

type PropertyDefinition = {
  id?: string;
  name?: string;
  label?: string;
  type?: string;
  isIdentifier?: boolean;
  is_identifier?: boolean;
  unit?: string;
  values?: unknown[];
  description?: string;
  source_field?: string;
  example?: unknown;
};
type OntologyNode = {
  id: string;
  labels: string[];
  properties: {
    name?: string;
    name_cn?: string;
    name_en?: string;
    description?: string;
    confidence?: number;
    source_fields?: string[];
    property_definitions?: PropertyDefinition[];
    instance_count?: number;
    instance_examples?: Array<Record<string, unknown>>;
    evidence_count?: number;
    evidence?: Record<string, unknown>;
  };
};
type OntologyEdge = {
  id: string;
  source: string;
  target: string;
  type: string;
  label?: string;
  properties?: {
    name?: string;
    description?: string;
    cardinality?: string;
    attributes?: PropertyDefinition[];
    source_fields?: string[];
    confidence?: number;
    evidence?: Record<string, unknown>;
  };
};
type Rule = {
  id: string;
  name: string;
  description?: string;
  formula?: string;
  condition?: unknown;
  effect?: unknown;
};
type GraphData = {
  nodes: OntologyNode[];
  edges: OntologyEdge[];
  logic_rules: Rule[];
  summary: {
    entity_type_count: number;
    property_count: number;
    relationship_count: number;
    logic_rule_count: number;
    instance_count: number;
    evidence_count: number;
  };
  error?: string;
};
type SearchItem = {
  kind: string;
  id: string;
  entity_id?: string;
  source?: string;
  target?: string;
  label?: string;
  description?: string;
};
type SearchResult = {
  groups?: Record<string, SearchItem[]>;
  results?: SearchItem[];
};

const colors = [
  "#0f4c81",
  "#087f5b",
  "#7d5a00",
  "#7c3aed",
  "#b42318",
  "#0e7490",
];
function stableColor(value: string) {
  let hash = 0;
  for (let index = 0; index < value.length; index += 1)
    hash = ((hash << 5) - hash + value.charCodeAt(index)) | 0;
  return colors[Math.abs(hash) % colors.length];
}

const semanticColors: Record<string, string> = {
  Episode: "#8b5cf6",
  Observation: "#2563eb",
  Machine: "#0f766e",
  SensorChannel: "#64748b",
  ProcessPhase: "#d97706",
  ToolCondition: "#e11d48",
  InspectionResult: "#16a34a",
};

function semanticKey(node: OntologyNode) {
  return node.properties.name_en || node.id.split(":").pop() || "";
}

function semanticNodeColor(node: OntologyNode) {
  return semanticColors[semanticKey(node)] || stableColor(String(node.properties.name || node.id));
}

function semanticGraphPositions(nodes: OntologyNode[]) {
  const positions: Record<string, { x: number; y: number }> = {};
  const slots: Record<string, { x: number; y: number }> = {
    Machine: { x: 120, y: 300 },
    Episode: { x: 390, y: 130 },
    Observation: { x: 390, y: 330 },
    SensorChannel: { x: 120, y: 530 },
    ProcessPhase: { x: 680, y: 130 },
    ToolCondition: { x: 680, y: 330 },
    InspectionResult: { x: 680, y: 530 },
  };
  const fallback = { x: 900, y: 130 };
  [...nodes]
    .sort((left, right) => (left.id < right.id ? -1 : left.id > right.id ? 1 : 0))
    .forEach((node, index) => {
    const key = semanticKey(node);
    positions[node.id] = slots[key] || { x: fallback.x, y: fallback.y + index * 100 };
    });
  return positions;
}

function uniqueVisualEdges(edges: OntologyEdge[]) {
  const seen = new Set<string>();
  return edges.filter((edge) => {
    const key = `${edge.source}|${edge.target}|${edge.type || edge.label || ""}`;
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function stringify(value: unknown) {
  if (value == null || value === "") return "—";
  if (
    typeof value === "string" ||
    typeof value === "number" ||
    typeof value === "boolean"
  )
    return String(value);
  return JSON.stringify(value, null, 2);
}

function PropertyRows({ properties }: { properties: PropertyDefinition[] }) {
  if (!properties.length)
    return <p className="text-xs text-slate-400">暂无属性定义</p>;
  return (
    <div className="space-y-2">
      {properties.map((property, index) => (
        <div
          key={property.id || `${property.name}-${index}`}
          className="rounded-lg border border-slate-200 bg-slate-50/70 px-3 py-2 text-xs"
        >
          <div className="flex items-center gap-2">
            <strong className="font-medium text-slate-800">
              {property.label || property.name || "未命名属性"}
            </strong>
            <span className="rounded border border-slate-200 bg-white px-1.5 py-0.5 font-mono text-[10px] text-slate-500">
              {property.type || "string"}
            </span>
            {(property.isIdentifier || property.is_identifier) && (
              <span className="rounded bg-emerald-100 px-1.5 py-0.5 text-[10px] text-emerald-800">
                标识符
              </span>
            )}
          </div>
          <div className="mt-1 grid gap-1 text-slate-500">
            {property.source_field && (
              <span>来源字段：{property.source_field}</span>
            )}
            {property.unit && <span>单位：{property.unit}</span>}
            {property.values && property.values.length > 0 && (
              <span>枚举：{property.values.map(stringify).join("、")}</span>
            )}
            {property.description && <span>{property.description}</span>}
            {property.example !== undefined && (
              <span>实例样例：{stringify(property.example)}</span>
            )}
          </div>
        </div>
      ))}
    </div>
  );
}

export default function GraphTabV2({ ontologyId, dataClass }: { ontologyId: string; dataClass?: string | null }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [searchParams] = useSearchParams();
  // FactoryNet keeps its semantic slots, while every ontology shares the same
  // readable node/edge treatment below.  In particular, regular and
  // multimodal ontologies must not fall back to the old straight-looking
  // graph presentation.
  const useTemporalLayout = dataClass === "temporal";
  const graphQuery = useQuery({ queryKey: ['ontology-graph', ontologyId], queryFn: ({ signal }) => apiClientV2.get<GraphData>(`/ontologies/${ontologyId}/graph`, { params: { view: 'ontology', limit: 1000 }, signal }) });
  const data = graphQuery.data;
  const loading = graphQuery.isPending;
  const error = graphQuery.isError ? '本体关系加载失败' : data?.error || '';
  const load = () => graphQuery.refetch();
  const [selected, setSelected] = useState<
    | { kind: "node"; value: OntologyNode }
    | { kind: "edge"; value: OntologyEdge }
    | null
  >(null);
  const [showRelationshipLabels, setShowRelationshipLabels] = useState(true);
  const {
    height: canvasHeight,
    beginResize: beginCanvasResize,
    handleKeyDown: handleCanvasResizeKeyDown,
    minHeight: canvasMinHeight,
    maxHeight: canvasMaxHeight,
  } = useResizableHeight();
  const [query, setQuery] = useState("");
  const urlNode = data?.nodes.find(node => node.id === searchParams.get('entity'));
  const selectedNode = selected?.kind === "node" ? selected.value : selected === null ? urlNode || null : null;
  const searchRequest = useQuery({
    queryKey: ['ontology-graph-search', ontologyId, query.trim(), selectedNode?.id],
    enabled: Boolean(query.trim()),
    queryFn: ({ signal }) => apiClientV2.get<SearchResult>(`/ontologies/${ontologyId}/search`, { params: { q: query.trim(), entity_id: selectedNode?.id }, signal }),
  });
  const searching = searchRequest.isFetching;
  const searchResult = query.trim() ? searchRequest.data : undefined;
  const hitIds = useMemo(() => {
    const ids = new Set<string>();
    for (const item of searchResult?.results || []) {
      if (item.kind === 'relationship') { ids.add(item.id); if(item.source) ids.add(item.source); if(item.target) ids.add(item.target); }
      else ids.add(item.entity_id || item.id);
    }
    return ids;
  }, [searchResult]);
  const selectedEdge = selected?.kind === "edge" ? selected.value : null;
  const nodeById = useMemo(
    () => new Map((data?.nodes || []).map((node) => [node.id, node])),
    [data],
  );
  const relatedEdges = useMemo(() => {
    if (!selectedNode) return [];
    const connected = (data?.edges || []).filter(
      (edge) => edge.source === selectedNode.id || edge.target === selectedNode.id,
    );
    return uniqueVisualEdges(connected);
  }, [data, selectedNode]);

  useEffect(() => {
    if (!data || !containerRef.current) return;
    // The relationship geometry is always present. The switch below only
    // controls the text labels, so hiding labels never makes the graph look
    // disconnected or changes what can be selected in the inspector.
    const visualEdges = uniqueVisualEdges(data.edges).sort((left, right) =>
      left.id < right.id ? -1 : left.id > right.id ? 1 : 0,
    );
    const orderedNodes = [...data.nodes].sort((left, right) =>
      left.id < right.id ? -1 : left.id > right.id ? 1 : 0,
    );
    const stablePositions = useTemporalLayout
      ? null
      : deterministicGraphPositions(
          orderedNodes,
          visualEdges,
          (node) => node.id,
          (edge) => edge.source,
          (edge) => edge.target,
        );
    const groupedEdges = new Map<string, OntologyEdge[]>();
    visualEdges.forEach((edge) => {
      const key = [edge.source, edge.target].sort().join("|");
      groupedEdges.set(key, [...(groupedEdges.get(key) || []), edge]);
    });
    const curveOffsets = new Map<string, number>();
    groupedEdges.forEach((group) => {
      group.forEach((edge, index) => {
        const centered = index - (group.length - 1) / 2;
        curveOffsets.set(edge.id, group.length === 1 ? 0 : centered * 82);
      });
    });
    const elements = [
      ...orderedNodes.map((node) => {
        const label = String(
          node.properties.name ||
            node.properties.name_cn ||
            node.properties.name_en ||
            node.id,
        ).slice(0, 20);
        const position = stablePositions?.get(node.id);
        return {
          data: {
            id: node.id,
            label,
            color: useTemporalLayout
              ? semanticNodeColor(node)
              : stableColor(String(node.properties.name || node.id)),
            width: Math.max(126, Math.min(178, label.length * 8 + 46)),
            height: label.length > 16 ? 70 : 58,
            textMaxWidth: Math.max(104, Math.min(148, label.length * 7 + 20)),
            raw: node,
          },
          ...(position ? { position } : {}),
        };
      }),
      ...visualEdges.map((edge) => {
        const curveOffset = curveOffsets.get(edge.id) || 0;
        const curveClass = `curve-${curveOffset < 0 ? "negative" : curveOffset > 0 ? "positive" : "center"}-${Math.abs(curveOffset)}`;
        return {
          data: {
            id: edge.id,
            source: edge.source,
            target: edge.target,
            label: String(edge.label || edge.type || "关联").replace(/_/g, " ").slice(0, 24),
            raw: edge,
          },
          classes: curveClass,
        };
      }),
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
            color: "#334155",
            "font-size": "9px",
            "text-valign": "bottom",
            "text-halign": "center",
            "text-margin-y": 5,
            width: 24,
            height: 24,
            shape: "ellipse",
            "text-wrap": "wrap",
            "text-max-width": "96px",
            "border-width": "1px",
            "border-color": "#ffffff",
          },
        },
        {
          selector: "edge",
          style: {
            label: showRelationshipLabels ? "data(label)" : "",
            color: "#475569",
            "font-size": "7px",
            "font-weight": 500,
            "text-rotation": "autorotate",
            "text-background-color": "#ffffff",
            "text-background-opacity": 0.94,
            "text-background-padding": "2px",
            "text-border-width": 1,
            "text-border-color": "#e2e8f0",
            "text-border-opacity": 0.95,
            "text-wrap": "ellipsis",
            "text-max-width": "96px",
            width: 1,
            "line-color": "#94a3b8",
            "target-arrow-color": "#94a3b8",
            "target-arrow-shape": "triangle",
            "arrow-scale": 0.65,
            "curve-style": "unbundled-bezier",
            "control-point-distance": 0,
            "control-point-weight": 0.5,
          },
        },
        ...Array.from(new Set(Array.from(curveOffsets.values()))).map((offset) => ({
          selector: `.curve-${offset < 0 ? "negative" : offset > 0 ? "positive" : "center"}-${Math.abs(offset)}`,
          style: { "control-point-distance": offset },
        })),
        {
          selector: ".active",
          style: {
            "background-color": "#0f172a",
            "text-outline-color": "#0f172a",
            "border-width": 3,
            "border-color": "#e2e8f0",
            "line-color": "#0f4c81",
            "target-arrow-color": "#0f4c81",
            width: 2.5,
          },
        },
        { selector: ".muted", style: { opacity: 0.15 } },
        {
          selector: ".search-hit",
          style: {
            "background-color": "#d97706",
            "text-outline-color": "#d97706",
            "line-color": "#d97706",
            "target-arrow-color": "#d97706",
            width: 2.5,
          },
        },
      ],
      layout: { name: "preset", fit: false },
    });
    if (useTemporalLayout) {
      const positions = semanticGraphPositions(data.nodes);
      cy.nodes().forEach((node) => {
        const position = positions[String(node.id())];
        if (position) node.position(position);
      });
      cy.resize();
      cy.fit(undefined, 54);
    } else {
      cy.resize();
      cy.fit(undefined, 64);
    }
    cy.on("tap", "node", (event) =>
      setSelected({
        kind: "node",
        value: event.target.data("raw") as OntologyNode,
      }),
    );
    cy.on("tap", "edge", (event) =>
      setSelected({
        kind: "edge",
        value: event.target.data("raw") as OntologyEdge,
      }),
    );
    cy.on("tap", (event) => {
      if (event.target === cy) setSelected(null);
    });
    cyRef.current = cy;
    const observer = typeof ResizeObserver !== "undefined" ? new ResizeObserver(() => {
      cy.resize();
      cy.fit(undefined, 64);
    }) : null;
    observer?.observe(containerRef.current);
    return () => {
      observer?.disconnect();
      cy.destroy();
      cyRef.current = null;
    };
  }, [data, searchParams, showRelationshipLabels, useTemporalLayout]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("active muted search-hit");
    if (selectedNode) {
      const focus = cy.getElementById(selectedNode.id);
      focus.addClass("active");
      focus.neighborhood().addClass("active");
      cy.elements().not(".active").addClass("muted");
    } else if (selected?.kind === "edge") {
      const focus = cy.getElementById(selected.value.id);
      focus.addClass("active");
      focus.connectedNodes().addClass("active");
      cy.elements().not(".active").addClass("muted");
    }
    if (hitIds.size) {
      let hits = cy.collection();
      hitIds.forEach((id) => {
        hits = hits.merge(cy.getElementById(id));
      });
      hits.addClass("search-hit");
      cy.elements().not(hits).addClass("muted");
    }
  }, [selected, selectedNode, hitIds, data]);
  const locate = (item: SearchItem) => {
    const entityId =
      item.entity_id || (item.kind === "relationship" ? item.source : item.id);
    const node = nodeById.get(entityId || "");
    if (node) {
      setSelected({ kind: "node", value: node });
      const cy = cyRef.current;
      if (cy)
        cy.animate({
          center: { eles: cy.getElementById(node.id) },
          duration: 220,
        });
    } else if (item.kind === "relationship") {
      const edge = data?.edges.find((value) => value.id === item.id);
      if (edge) setSelected({ kind: "edge", value: edge });
    }
  };
  if (loading)
    return (
      <div className="flex min-h-[420px] items-center justify-center text-sm text-slate-500">
        <Loader2 size={17} className="mr-2 animate-spin" />
        正在加载本体
      </div>
    );
  if (error && !data)
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-5 text-sm text-red-700">
        <div className="flex items-center gap-2 font-medium">
          <CircleAlert size={16} />
          {error}
        </div>
        <button
          className="mt-3 rounded border border-red-200 bg-white px-3 py-1.5 text-xs"
          onClick={load}
        >
          重试
        </button>
      </div>
    );
  const summary = data?.summary || {
    entity_type_count: 0,
    property_count: 0,
    relationship_count: 0,
    logic_rule_count: 0,
    instance_count: 0,
    evidence_count: 0,
  };
  return (
    <div
      className="grid items-start gap-4 xl:grid-cols-[minmax(0,1fr)_360px]"
      data-testid="ontology-canvas"
    >
      <section className="relative overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">本体关系</p>
            <p className="mt-0.5 text-xs text-slate-500">实体类型与关系 · {showRelationshipLabels ? "关系文字已显示" : "关系文字已隐藏"}</p>
          </div>
          <div className="flex items-center gap-2">
            <button
              onClick={() => setShowRelationshipLabels((value) => !value)}
              className={`rounded-lg border px-3 py-1.5 text-xs ${showRelationshipLabels ? "border-slate-300 bg-white text-slate-700" : "border-slate-800 bg-slate-800 text-white"}`}
              title={showRelationshipLabels ? "隐藏关系文字，保留关系线" : "显示关系文字"}
              aria-pressed={showRelationshipLabels}
              data-testid="toggle-relationships"
            >
              {showRelationshipLabels ? "隐藏关系文字" : "显示关系文字"}
            </button>
            <button
              onClick={load}
              className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"
              title="刷新本体"
            >
              <RefreshCw size={15} />
            </button>
          </div>
        </div>
        {error && (
          <div className="mx-4 mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {error}
          </div>
        )}
        {!data?.nodes.length ? (
          <div
            className="flex flex-col items-center justify-center text-slate-400"
            style={{ height: canvasHeight }}
          >
            <Database size={24} />
            <p className="mt-3 text-sm">尚未发布实体类型与关系</p>
          </div>
        ) : (
          <div ref={containerRef} className="w-full" style={{ height: canvasHeight }} />
        )}
        <div
          role="separator"
          aria-orientation="horizontal"
          aria-label="调整本体关系画布高度"
          aria-valuemin={canvasMinHeight}
          aria-valuemax={canvasMaxHeight}
          aria-valuenow={canvasHeight}
          tabIndex={0}
          data-testid="resize-ontology-canvas"
          onPointerDown={beginCanvasResize}
          onKeyDown={handleCanvasResizeKeyDown}
          className="group flex h-4 cursor-row-resize items-center justify-center border-t border-slate-200 bg-white transition-colors hover:bg-slate-50 focus:outline-none focus:ring-2 focus:ring-inset focus:ring-slate-300"
          title="拖动调整关系图高度；也可以聚焦后使用上下方向键"
        >
          <span className="h-1 w-16 rounded-full bg-slate-300 transition-colors group-hover:bg-slate-500" />
        </div>
        <p className="px-4 pb-2 text-[10px] text-slate-400">
          拖动底部横线调整展示高度
        </p>
      </section>
      <aside className="flex min-h-[650px] flex-col rounded-xl border border-slate-200 bg-white">
        <div className="border-b border-slate-200 px-4 py-3">
          <p className="text-sm font-semibold text-slate-900">详细信息</p>
          <p className="mt-0.5 text-xs text-slate-500">
            {selectedNode ? "当前实体" : selectedEdge ? "当前关系" : "本体概览"}
          </p>
        </div>
        <div className="min-h-0 flex-1 overflow-y-auto px-4 py-4">
          {!selected && (
            <div className="space-y-3">
              <div className="grid grid-cols-2 gap-2 text-xs">
                {[
                  ["实体类型", summary.entity_type_count],
                  ["属性", summary.property_count],
                  ["关系", summary.relationship_count],
                  ["逻辑规则", summary.logic_rule_count],
                  ["真实实例", summary.instance_count],
                  ["证据", summary.evidence_count],
                ].map(([label, value]) => (
                  <div
                    key={String(label)}
                    className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2"
                  >
                    <span className="block text-slate-500">{label}</span>
                    <strong className="mt-1 block text-base text-slate-800">
                      {value}
                    </strong>
                  </div>
                ))}
              </div>
              <p className="pt-2 text-xs leading-5 text-slate-500">
                点击画布中的实体或关系，查看属性、关联关系和来源证据。
              </p>
            </div>
          )}
          {selectedNode && (
            <div className="space-y-5">
              <div>
                <h3 className="text-base font-semibold text-slate-900">
                  {selectedNode.properties.name}
                </h3>
                {selectedNode.properties.name_en &&
                  selectedNode.properties.name_en !==
                    selectedNode.properties.name && (
                    <p className="mt-1 text-xs text-slate-500">
                      {selectedNode.properties.name_en}
                    </p>
                  )}
                <p className="mt-2 text-sm leading-5 text-slate-600">
                  {selectedNode.properties.description || "暂无说明"}
                </p>
                <div className="mt-3 flex flex-wrap gap-2 text-[11px]">
                  <span className="rounded bg-slate-100 px-2 py-1 text-slate-600">
                    置信度{" "}
                    {Number(selectedNode.properties.confidence ?? 1).toFixed(2)}
                  </span>
                  <span className="rounded bg-slate-100 px-2 py-1 text-slate-600">
                    真实实例 {selectedNode.properties.instance_count || 0}
                  </span>
                  <span className="rounded bg-slate-100 px-2 py-1 text-slate-600">
                    证据 {selectedNode.properties.evidence_count || 0}
                  </span>
                </div>
              </div>
              <section>
                <h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">
                  属性
                </h4>
                <PropertyRows
                  properties={
                    selectedNode.properties.property_definitions || []
                  }
                />
              </section>
              <section>
                <h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">
                  关系
                </h4>
                {relatedEdges.length ? (
                  <div className="space-y-2">
                    {relatedEdges.map((edge) => {
                      const other = nodeById.get(
                        edge.source === selectedNode.id
                          ? edge.target
                          : edge.source,
                      );
                      return (
                        <button
                          key={edge.id}
                          onClick={() =>
                            setSelected({ kind: "edge", value: edge })
                          }
                          className="w-full rounded-lg border border-slate-200 px-3 py-2 text-left text-xs hover:border-slate-400"
                        >
                          <strong className="block text-slate-800">
                            {edge.label || edge.type}
                          </strong>
                          <span className="mt-1 block text-slate-500">
                            {edge.source === selectedNode.id ? "指向" : "来自"}{" "}
                            {other?.properties.name ||
                              other?.id ||
                              "未解析实体"}{" "}
                            · {edge.properties?.cardinality || "one-to-many"}
                          </span>
                        </button>
                      );
                    })}
                  </div>
                ) : (
                  <p className="text-xs text-slate-400">暂无关联关系</p>
                )}
              </section>
              <section>
                <h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">
                  来源证据
                </h4>
                <div className="rounded-lg border border-slate-200 bg-slate-50 px-3 py-2 text-xs text-slate-600">
                  来源字段：
                  {selectedNode.properties.source_fields?.length
                    ? selectedNode.properties.source_fields.join("、")
                    : "—"}
                </div>
              </section>
            </div>
          )}
          {selectedEdge && (
            <div className="space-y-4">
              <div>
                <h3 className="text-base font-semibold text-slate-900">
                  {selectedEdge.label || selectedEdge.type}
                </h3>
                <p className="mt-2 text-sm text-slate-600">
                  {selectedEdge.properties?.description || "暂无说明"}
                </p>
              </div>
              <div className="rounded-lg border border-slate-200 bg-slate-50 p-3 text-xs text-slate-600">
                <p>
                  起点：
                  {nodeById.get(selectedEdge.source)?.properties.name ||
                    selectedEdge.source}
                </p>
                <p className="mt-1">
                  终点：
                  {nodeById.get(selectedEdge.target)?.properties.name ||
                    selectedEdge.target}
                </p>
                <p className="mt-1">
                  基数：{selectedEdge.properties?.cardinality || "one-to-many"}
                </p>
              </div>
              <section>
                <h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">
                  关系属性
                </h4>
                <PropertyRows
                  properties={selectedEdge.properties?.attributes || []}
                />
              </section>
              <section>
                <h4 className="mb-2 text-xs font-semibold tracking-wide text-slate-500">
                  来源证据
                </h4>
                <p className="text-xs text-slate-600">
                  来源字段：
                  {selectedEdge.properties?.source_fields?.join("、") || "—"}
                </p>
              </section>
            </div>
          )}
        </div>
        <div className="border-t border-slate-200 p-3">
          <div className="relative">
            <Search
              size={15}
              className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
            />
            <input
              value={query}
              onChange={(event) => setQuery(event.target.value)}
              placeholder={selectedNode ? "搜索当前实体的属性" : "搜索本体"}
              className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-8 text-sm outline-none focus:border-slate-700"
            />
            {query && (
              <button
                onClick={() => setQuery("")}
                className="absolute right-2 top-1/2 -translate-y-1/2 text-slate-400"
              >
                <X size={15} />
              </button>
            )}
          </div>
          {searching && (
            <p className="mt-2 flex items-center gap-1 text-xs text-slate-500">
              <Loader2 size={12} className="animate-spin" />
              正在搜索
            </p>
          )}
          {searchRequest.isError && <p role="alert">搜索失败，请重试。<button onClick={() => void searchRequest.refetch()}>重试</button></p>}
          {query && !searching && !searchRequest.isError && (
            <div className="mt-2 max-h-40 space-y-2 overflow-y-auto">
              {Object.entries(searchResult?.groups || {}).map(
                ([group, items]) => (
                  <div key={group}>
                    <p className="mb-1 text-[10px] font-semibold uppercase tracking-wide text-slate-400">
                      {group}
                    </p>
                    {items.map((item, index) => (
                      <button
                        key={`${item.kind}-${item.id}-${index}`}
                        onClick={() => locate(item)}
                        className="block w-full rounded px-2 py-1.5 text-left text-xs hover:bg-slate-50"
                      >
                        <strong className="block text-slate-700">
                          {item.label || item.id}
                        </strong>
                        {item.description && (
                          <span className="block truncate text-slate-500">
                            {item.description}
                          </span>
                        )}
                      </button>
                    ))}
                  </div>
                ),
              )}
              {!(searchResult?.results || []).length && (
                <p className="text-xs text-slate-400">无匹配结果</p>
              )}
            </div>
          )}
        </div>
      </aside>
    </div>
  );
}
