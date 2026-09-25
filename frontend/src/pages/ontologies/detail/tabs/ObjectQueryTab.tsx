import { lazy, Suspense, useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { createColumnHelper, tableFeatures, useTable } from "@tanstack/react-table";
import { QueryBuilder, type Field, type RuleGroupType } from "react-querybuilder";
import { BarChart3, ChevronLeft, ChevronRight, Filter, List, RefreshCw, Search, X } from "lucide-react";
import { useSearchParams } from "react-router-dom";

import { apiClientV2 } from "@/api/client";
import ObjectPanel, { type ObjectPanelRecord } from "../object-explorer/ObjectPanel";
import {
  EMPTY_QUERY,
  compileObjectSetExpression,
  readExplorerUrl,
  writeExplorerUrl,
  type ExplorerMode,
  type ExplorerProperty,
} from "../object-explorer/contract";
import "../object-explorer/object-explorer.css";

const ObjectDistributionChart = lazy(() => import("../object-explorer/ObjectDistributionChart"));

type Property = { id?: string; api_name?: string; name?: string; label?: string; type?: string; data_type?: string; values?: unknown[] };
type Entity = {
  id: string;
  name?: string;
  name_cn?: string;
  name_en?: string;
  type?: string;
  api_name?: string;
  properties?: Property[];
};
type LoadResult = {
  objects: ObjectPanelRecord[];
  page: { page_size: number; returned: number; has_more: boolean; next_page_token?: string | null; stability?: string };
  completeness: string;
  status: string;
  definition_hash?: string;
  execution_hash?: string;
};

const tableFeatureSet = tableFeatures({});
const columnHelper = createColumnHelper<typeof tableFeatureSet, ObjectPanelRecord>();
const EMPTY_OBJECTS: ObjectPanelRecord[] = [];

const comparisonOperators = [
  { name: "=", label: "等于" }, { name: "!=", label: "不等于" },
  { name: ">", label: "大于" }, { name: ">=", label: "大于等于" },
  { name: "<", label: "小于" }, { name: "<=", label: "小于等于" },
  { name: "between", label: "介于" }, { name: "null", label: "为空" }, { name: "notNull", label: "不为空" },
];
const textOperators = [
  { name: "=", label: "等于" }, { name: "!=", label: "不等于" },
  { name: "contains", label: "包含" }, { name: "doesNotContain", label: "不包含" },
  { name: "beginsWith", label: "开头是" }, { name: "in", label: "属于列表" },
  { name: "null", label: "为空" }, { name: "notNull", label: "不为空" },
];
const booleanOperators = [
  { name: "=", label: "等于" }, { name: "!=", label: "不等于" },
  { name: "null", label: "为空" }, { name: "notNull", label: "不为空" },
];
const arrayOperators = [
  { name: "containsAny", label: "包含任一" }, { name: "containsAll", label: "包含全部" },
  { name: "null", label: "为空" }, { name: "notNull", label: "不为空" },
];

function entityApiName(entity: Entity): string {
  return entity.api_name || entity.name_en || (entity.type && entity.type !== "EntityType" ? entity.type : "") || entity.name || entity.name_cn || entity.id;
}

function toExplorerProperty(property: Property): ExplorerProperty | null {
  const apiName = property.api_name || property.id || property.name || "";
  if (!apiName) return null;
  const dataType = String(property.data_type || property.type || "string").toLowerCase();
  return {
    apiName,
    label: property.label || property.name || apiName,
    dataType,
    values: property.values?.map((value) => ({ name: String(value), label: String(value) })),
  };
}

function queryBuilderField(property: ExplorerProperty): Field {
  const numericOrTemporal = ["integer", "long", "double", "decimal", "float", "number", "date", "datetime", "timestamp"].includes(property.dataType);
  const isBoolean = property.dataType === "boolean";
  const isArray = property.dataType === "array";
  return {
    name: property.apiName,
    label: property.label,
    inputType: property.dataType === "date" ? "date" : ["datetime", "timestamp"].includes(property.dataType) ? "datetime-local" : numericOrTemporal ? "number" : undefined,
    valueEditorType: property.values?.length || isBoolean ? "select" : "text",
    values: property.values?.length ? property.values : isBoolean ? [{ name: "true", label: "True" }, { name: "false", label: "False" }] : undefined,
    operators: isArray ? arrayOperators : isBoolean ? booleanOperators : numericOrTemporal ? comparisonOperators : textOperators,
  };
}

function valueLabel(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function ObjectQueryTab({ ontologyId }: { ontologyId: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [restored] = useState(() => readExplorerUrl(searchParams));
  const [typeName, setTypeName] = useState(restored.objectType);
  const [mode, setMode] = useState<ExplorerMode>(restored.mode);
  const [draftQuery, setDraftQuery] = useState<RuleGroupType>(restored.query);
  const [appliedQuery, setAppliedQuery] = useState<RuleGroupType>(restored.query);
  const [pageTokens, setPageTokens] = useState<Array<string | null>>([null]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);

  const entities = useQuery({
    queryKey: ["object-query-types", ontologyId],
    queryFn: () => apiClientV2.get<{ entities: Entity[] }>(`/ontologies/${ontologyId}/entities`),
  });
  const types = useMemo(() => entities.data?.entities || [], [entities.data?.entities]);
  const selectedType = useMemo(() => types.find((entity) => entityApiName(entity) === typeName) || types[0], [typeName, types]);
  const selected = selectedType ? entityApiName(selectedType) : "";
  const properties = useMemo(() => (selectedType?.properties || []).map(toExplorerProperty).filter((item): item is ExplorerProperty => item !== null), [selectedType]);
  const fields = useMemo(() => properties.map(queryBuilderField), [properties]);
  const compiled = useMemo(() => compileObjectSetExpression(selected, appliedQuery, properties), [appliedQuery, properties, selected]);
  const currentPage = pageTokens.length - 1;
  const pageToken = pageTokens[currentPage];

  const result = useQuery({
    queryKey: ["object-query-load", ontologyId, compiled.expression, pageToken],
    enabled: !!selected && entities.isSuccess && compiled.issues.length === 0,
    queryFn: () => apiClientV2.post<LoadResult>(`/ontologies/${ontologyId}/object-query/load`, {
      expression: compiled.expression,
      context: { ontology_id: ontologyId, consistency: "live" },
      read: { page_size: 50, page_token: pageToken },
    }),
  });
  const objects = result.data?.objects || EMPTY_OBJECTS;
  const preview = objects.find((object) => object.object_id === previewId) || null;
  const visiblePropertyNames = useMemo(() => {
    const metadataNames = properties.map((property) => property.apiName);
    const observed = objects.flatMap((object) => Object.keys(object.properties));
    return [...new Set([...metadataNames, ...observed])].slice(0, 8);
  }, [objects, properties]);
  const columns = useMemo(() => columnHelper.columns([
    columnHelper.display({
      id: "select",
      header: "",
      cell: ({ row }) => <input type="checkbox" aria-label={`选择 ${row.original.object_id}`} checked={selectedIds.includes(row.original.object_id)} onChange={() => setSelectedIds((ids) => ids.includes(row.original.object_id) ? ids.filter((id) => id !== row.original.object_id) : [...ids, row.original.object_id])} />,
    }),
    columnHelper.accessor((row) => row.object_id, { id: "object_id", header: "Object", cell: ({ row }) => <button type="button" className="oe-object-link" onClick={() => setPreviewId(row.original.object_id)}>{row.original.object_id}</button> }),
    ...visiblePropertyNames.map((name) => columnHelper.accessor((row) => row.properties[name], { id: name, header: name, cell: (info) => <span title={valueLabel(info.getValue())}>{valueLabel(info.getValue())}</span> })),
  ]), [selectedIds, visiblePropertyNames]);
  const table = useTable({ features: tableFeatureSet, data: objects, columns });

  const apply = () => {
    const next = compileObjectSetExpression(selected, draftQuery, properties);
    if (next.issues.length) return;
    setAppliedQuery(draftQuery);
    setPageTokens([null]);
    setSelectedIds([]);
    setPreviewId(null);
    setSearchParams(writeExplorerUrl(searchParams, { objectType: selected, mode, query: draftQuery }), { replace: true });
  };
  const switchMode = (nextMode: ExplorerMode) => {
    setMode(nextMode);
    setSearchParams(writeExplorerUrl(searchParams, { objectType: selected, mode: nextMode, query: appliedQuery }), { replace: true });
  };
  const switchType = (nextType: string) => {
    setTypeName(nextType);
    setDraftQuery(EMPTY_QUERY);
    setAppliedQuery(EMPTY_QUERY);
    setPageTokens([null]);
    setSelectedIds([]);
    setPreviewId(null);
    setSearchParams(writeExplorerUrl(searchParams, { objectType: nextType, mode, query: EMPTY_QUERY }), { replace: true });
  };
  const clearFilters = () => {
    setDraftQuery(EMPTY_QUERY);
    setAppliedQuery(EMPTY_QUERY);
    setPageTokens([null]);
    setSearchParams(writeExplorerUrl(searchParams, { objectType: selected, mode, query: EMPTY_QUERY }), { replace: true });
  };

  if (entities.isPending) return <div role="status" className="rounded border p-4 text-sm text-slate-500">正在加载对象类型</div>;
  if (entities.error) return <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">对象类型加载失败。<button onClick={() => void entities.refetch()}>重试</button></div>;
  if (!selected) return <div role="status">暂无可查询的对象类型。</div>;

  const draftIssues = compileObjectSetExpression(selected, draftQuery, properties).issues;
  return (
    <section aria-label="Object Explorer" className="oe-shell">
      <header className="oe-command-bar">
        <div className="oe-type-mark">{(selectedType?.name_cn || selectedType?.name || selected).slice(0, 1).toUpperCase()}</div>
        <label className="oe-type-select">
          <span>Object type</span>
          <select aria-label="对象类型" value={selected} onChange={(event) => switchType(event.target.value)}>
            {types.map((entity) => <option key={entity.id} value={entityApiName(entity)}>{entity.name_cn || entity.name || entity.name_en || entity.id}</option>)}
          </select>
        </label>
        <div className="oe-query-summary"><Search size={14} /><span>{compiled.activeRuleCount ? `${compiled.activeRuleCount} 个筛选条件` : "所有对象"}</span></div>
        {compiled.activeRuleCount > 0 && <button type="button" className="oe-clear" onClick={clearFilters}><X size={13} />Clear</button>}
        <div className="oe-command-actions">
          <button type="button" disabled title="静态 List 将在保存资源阶段接入">List</button>
          <button type="button" disabled title="动态 Exploration 将在保存资源阶段接入">Exploration</button>
          <button type="button" disabled title="Compare 需要两个 pinned data view">Compare</button>
        </div>
      </header>

      <div className="oe-filter-panel">
        <div className="oe-filter-title"><Filter size={15} /><div><strong>Filters</strong><span>由 ontology metadata 限制字段和操作符</span></div></div>
        {fields.length ? (
          <div className="oe-query-builder"><QueryBuilder fields={fields} query={draftQuery} onQueryChange={setDraftQuery} showCombinatorsBetweenRules showNotToggle /></div>
        ) : <p className="oe-empty-copy">该 Object Type 没有已发布的 property metadata，只能加载基础集合。</p>}
        <div className="oe-filter-footer">
          <div>{draftIssues.length ? <span role="alert" className="text-red-600">{draftIssues[0]}</span> : <span>筛选只在点击 Apply 后执行；UI 不直接解释后端查询。</span>}</div>
          <button type="button" onClick={apply} disabled={draftIssues.length > 0}>Apply filters</button>
        </div>
      </div>

      <div className="oe-workspace-tabs" role="tablist">
        <button type="button" role="tab" aria-selected={mode === "explore"} onClick={() => switchMode("explore")} className={mode === "explore" ? "active" : ""}><BarChart3 size={14} />Explore</button>
        <button type="button" role="tab" aria-selected={mode === "results"} onClick={() => switchMode("results")} className={mode === "results" ? "active" : ""}><List size={14} />Results</button>
        <span className="oe-result-meta">{result.data?.page.returned ?? 0} visible · {result.data?.completeness || "loading"}</span>
        <button type="button" className="oe-refresh" onClick={() => void result.refetch()} aria-label="刷新对象集合"><RefreshCw size={14} /></button>
      </div>

      {result.isLoading && <div role="status" className="oe-state">正在读取对象集合…</div>}
      {result.error && <div role="alert" className="oe-state oe-state--error">对象查询失败。<button onClick={() => void result.refetch()}>重试</button></div>}
      {compiled.issues.length > 0 && <div role="alert" className="oe-state oe-state--error">{compiled.issues.join("；")}</div>}

      {result.data && <div className={`oe-content ${preview ? "has-preview" : ""}`}>
        <main className="oe-main">
          {mode === "explore" ? (
            <div className="oe-chart-card">
              <div className="oe-chart-card__header"><div><span>当前页分布</span><strong>{visiblePropertyNames[0] || "No property"}</strong></div><span>Preview · 非完整聚合</span></div>
              <Suspense fallback={<div className="oe-chart-empty">正在加载图表渲染器…</div>}>
                <ObjectDistributionChart objects={objects} property={visiblePropertyNames[0] || ""} />
              </Suspense>
              <p className="oe-chart-note">这里只呈现当前返回页。完整统计必须使用 pinned data view 的 aggregate API。</p>
            </div>
          ) : (
            <div className="oe-table-wrap">
              <table className="oe-results-table">
                <thead>{table.getHeaderGroups().map((group) => <tr key={group.id}>{group.headers.map((header) => <th key={header.id}>{header.isPlaceholder ? null : <table.FlexRender header={header} />}</th>)}</tr>)}</thead>
                <tbody>{table.getRowModel().rows.map((row) => <tr key={row.id} className={selectedIds.includes(row.original.object_id) ? "selected" : ""}>{row.getAllCells().map((cell) => <td key={cell.id}><table.FlexRender cell={cell} /></td>)}</tr>)}</tbody>
              </table>
              {!objects.length && <div className="oe-state">当前条件没有对象。</div>}
            </div>
          )}
          <footer className="oe-pagination">
            <span>{selectedIds.length} selected · 状态 {result.data.status} · {result.data.page.stability || "live"}</span>
            <div>
              <button type="button" disabled={currentPage === 0} onClick={() => { setPageTokens((tokens) => tokens.slice(0, -1)); setPreviewId(null); }}><ChevronLeft size={14} />上一页</button>
              <span>第 {currentPage + 1} 页</span>
              <button type="button" disabled={!result.data.page.has_more || !result.data.page.next_page_token} onClick={() => { setPageTokens((tokens) => [...tokens, result.data.page.next_page_token || null]); setPreviewId(null); }}>下一页<ChevronRight size={14} /></button>
            </div>
          </footer>
        </main>
        {preview && <ObjectPanel object={preview} onClose={() => setPreviewId(null)} />}
      </div>}
    </section>
  );
}
