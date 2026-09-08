import { useCallback, useEffect, useMemo, useRef, useState } from "react";
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

export default function GraphTabV2({ ontologyId }: { ontologyId: string }) {
  const containerRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  const [searchParams] = useSearchParams();
  const [data, setData] = useState<GraphData | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState("");
  const [selected, setSelected] = useState<
    | { kind: "node"; value: OntologyNode }
    | { kind: "edge"; value: OntologyEdge }
    | null
  >(null);
  const [query, setQuery] = useState("");
  const [searching, setSearching] = useState(false);
  const [searchResult, setSearchResult] = useState<SearchResult | null>(null);
  const [hitIds, setHitIds] = useState<Set<string>>(new Set());
  const load = useCallback(async () => {
    setLoading(true);
    setError("");
    try {
      const result = await apiClientV2.get<GraphData>(
        `/ontologies/${ontologyId}/graph`,
        { params: { view: "ontology", limit: 1000 } },
      );
      setData(result);
      if (result.error) setError(result.error);
    } catch (err: any) {
      setData(null);
      setError(
        err?.response?.data?.detail ||
          err?.detail ||
          err?.message ||
          "本体关系加载失败",
      );
    } finally {
      setLoading(false);
    }
  }, [ontologyId]);
  useEffect(() => {
    load();
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
        ? (data?.edges || []).filter(
            (edge) =>
              edge.source === selectedNode.id ||
              edge.target === selectedNode.id,
          )
        : [],
    [data, selectedNode],
  );

  useEffect(() => {
    if (!data || !containerRef.current) return;
    const elements = [
      ...data.nodes.map((node) => ({
        data: {
          id: node.id,
          label: String(
            node.properties.name ||
              node.properties.name_cn ||
              node.properties.name_en ||
              node.id,
          ).slice(0, 28),
          color: stableColor(String(node.properties.name || node.id)),
          raw: node,
        },
      })),
      ...data.edges.map((edge) => ({
        data: {
          id: edge.id,
          source: edge.source,
          target: edge.target,
          label: `${edge.label || edge.type}${edge.properties?.cardinality ? ` · ${edge.properties.cardinality}` : ""}`,
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
            "font-size": "11px",
            "font-weight": "bold",
            "text-valign": "center",
            "text-halign": "center",
            width: "82px",
            height: "52px",
            shape: "round-rectangle",
            "text-wrap": "wrap",
            "text-max-width": "68px",
            "text-outline-width": "2px",
            "text-outline-color": "data(color)",
            "border-width": "1px",
            "border-color": "#ffffff",
          },
        },
        {
          selector: "edge",
          style: {
            label: "data(label)",
            "font-size": "9px",
            color: "#475569",
            width: 1.5,
            "line-color": "#94a3b8",
            "target-arrow-color": "#94a3b8",
            "target-arrow-shape": "triangle",
            "curve-style": "bezier",
            "text-background-color": "#f8fafc",
            "text-background-opacity": 0.96,
            "text-background-padding": "2px",
            "text-rotation": "autorotate",
          },
        },
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
      layout: {
        name: "cose",
        animate: false,
        randomize: false,
        componentSpacing: 100,
        idealEdgeLength: 160,
        nodeRepulsion: 8500,
        numIter: 1400,
      } as any,
    });
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
    const entityFromUrl = searchParams.get("entity");
    if (entityFromUrl && data.nodes.some((node) => node.id === entityFromUrl))
      setSelected({
        kind: "node",
        value: data.nodes.find((node) => node.id === entityFromUrl)!,
      });
    return () => {
      cy.destroy();
      cyRef.current = null;
    };
  }, [data, searchParams]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy) return;
    cy.elements().removeClass("active muted search-hit");
    if (selected?.kind === "node") {
      const focus = cy.getElementById(selected.value.id);
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
  }, [selected, hitIds]);
  useEffect(() => {
    const trimmed = query.trim();
    if (!trimmed) {
      setSearchResult(null);
      setHitIds(new Set());
      return;
    }
    const timer = window.setTimeout(async () => {
      setSearching(true);
      try {
        const result = await apiClientV2.get<SearchResult>(
          `/ontologies/${ontologyId}/search`,
          { params: { q: trimmed, entity_id: selectedNode?.id } },
        );
        setSearchResult(result);
        const ids = new Set<string>();
        for (const item of result.results || []) {
          if (item.kind === "relationship") {
            ids.add(item.id);
            ids.add(item.source || "");
            ids.add(item.target || "");
          } else ids.add(item.entity_id || item.id);
        }
        ids.delete("");
        setHitIds(ids);
      } catch {
        setSearchResult({ groups: { 搜索: [] }, results: [] });
        setHitIds(new Set());
      } finally {
        setSearching(false);
      }
    }, 220);
    return () => window.clearTimeout(timer);
  }, [ontologyId, query, selectedNode?.id]);
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
      className="grid min-h-[650px] gap-4 xl:grid-cols-[minmax(0,1fr)_360px]"
      data-testid="ontology-canvas"
    >
      <section className="relative overflow-hidden rounded-xl border border-slate-200 bg-white">
        <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3">
          <div>
            <p className="text-sm font-semibold text-slate-900">本体关系</p>
            <p className="mt-0.5 text-xs text-slate-500">实体类型与关系</p>
          </div>
          <button
            onClick={load}
            className="rounded-lg border border-slate-200 p-2 text-slate-500 hover:bg-slate-50"
            title="刷新本体"
          >
            <RefreshCw size={15} />
          </button>
        </div>
        {error && (
          <div className="mx-4 mt-3 rounded-md border border-amber-200 bg-amber-50 px-3 py-2 text-xs text-amber-800">
            {error}
          </div>
        )}
        {!data?.nodes.length ? (
          <div className="flex h-[560px] flex-col items-center justify-center text-slate-400">
            <Database size={24} />
            <p className="mt-3 text-sm">尚未发布实体类型与关系</p>
          </div>
        ) : (
          <div ref={containerRef} className="h-[590px] w-full" />
        )}
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
          {query && !searching && (
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
