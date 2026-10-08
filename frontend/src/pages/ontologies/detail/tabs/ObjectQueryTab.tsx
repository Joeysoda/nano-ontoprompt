import { explorerError } from '../object-explorer/runtime';
import { lazy, Suspense, useCallback, useMemo, useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { createColumnHelper, tableFeatures, useTable } from '@tanstack/react-table';
import { QueryBuilder, type Field, type RuleGroupType } from 'react-querybuilder';
import {
  BarChart3,
  ChevronDown,
  ChevronLeft,
  ChevronRight,
  ChevronUp,
  Columns3,
  Filter,
  GripVertical,
  LayoutGrid,
  List,
  Maximize2,
  Minimize2,
  Plus,
  RefreshCw,
  RotateCcw,
  Search,
  Settings2,
  Trash2,
  X,
} from 'lucide-react';
import { useSearchParams } from 'react-router-dom';

import { apiClientV2 } from '@/api/client';
import ObjectPanel, { type ObjectPanelRecord } from '../object-explorer/ObjectPanel';
import ExplorerOperations, { type Catalog, type ExplorerContext } from '../object-explorer/ExplorerOperations';
import ExplorerAnalysis from '../object-explorer/ExplorerAnalysis';
import {
  EMPTY_QUERY,
  compileObjectSetExpression,
  readExplorerUrl,
  writeExplorerUrl,
  filterExpression,
  type ObjectSetExpression,
  type ExplorerMode,
  type ExplorerProperty,
} from '../object-explorer/contract';
import '../object-explorer/object-explorer.css';

const ObjectDistributionChart = lazy(() => import('../object-explorer/ObjectDistributionChart'));
const ObjectGraph = lazy(() => import('../object-explorer/ObjectGraph'));

type Property = { id?: string; api_name?: string; name?: string; label?: string; type?: string; data_type?: string; values?: unknown[] };
type Entity = { id: string; name?: string; name_cn?: string; name_en?: string; type?: string; api_name?: string; properties?: Property[] };
type LoadResult = {
  objects: ObjectPanelRecord[];
  page: { page_size: number; returned: number; has_more: boolean; next_page_token?: string | null; stability?: string };
  completeness: string;
  status: string;
  definition_hash?: string;
  execution_hash?: string;
};
type SortState = { property: string; direction: 'asc' | 'desc' } | null;

const tableFeatureSet = tableFeatures({});
const columnHelper = createColumnHelper<typeof tableFeatureSet, ObjectPanelRecord>();
const EMPTY_OBJECTS: ObjectPanelRecord[] = [];

const comparisonOperators = [
  { name: '=', label: '等于' }, { name: '!=', label: '不等于' },
  { name: '>', label: '大于' }, { name: '>=', label: '大于等于' },
  { name: '<', label: '小于' }, { name: '<=', label: '小于等于' },
  { name: 'between', label: '介于' }, { name: 'null', label: '为空' }, { name: 'notNull', label: '不为空' },
];
const textOperators = [
  { name: '=', label: '等于' }, { name: '!=', label: '不等于' },
  { name: 'contains', label: '包含' }, { name: 'doesNotContain', label: '不包含' },
  { name: 'beginsWith', label: '开头是' }, { name: 'in', label: '属于列表' },
  { name: 'null', label: '为空' }, { name: 'notNull', label: '不为空' },
];
const booleanOperators = [
  { name: '=', label: '等于' }, { name: '!=', label: '不等于' },
  { name: 'null', label: '为空' }, { name: 'notNull', label: '不为空' },
];
const arrayOperators = [
  { name: 'containsAny', label: '包含任一' }, { name: 'containsAll', label: '包含全部' },
  { name: 'null', label: '为空' }, { name: 'notNull', label: '不为空' },
];

function entityApiName(entity: Entity): string {
  return entity.api_name || entity.name_en || (entity.type && entity.type !== 'EntityType' ? entity.type : '') || entity.name || entity.name_cn || entity.id;
}

function toExplorerProperty(property: Property): ExplorerProperty | null {
  const apiName = property.api_name || property.id || property.name || '';
  if (!apiName) return null;
  const dataType = String(property.data_type || property.type || 'string').toLowerCase();
  return { apiName, label: property.label || property.name || apiName, dataType, values: property.values?.map(value => ({ name: String(value), label: String(value) })) };
}

function queryBuilderField(property: ExplorerProperty): Field {
  const numericOrTemporal = ['integer', 'long', 'double', 'decimal', 'float', 'number', 'date', 'datetime', 'timestamp'].includes(property.dataType);
  const isBoolean = property.dataType === 'boolean';
  const isArray = property.dataType === 'array';
  return {
    name: property.apiName,
    label: property.label,
    inputType: property.dataType === 'date' ? 'date' : ['datetime', 'timestamp'].includes(property.dataType) ? 'datetime-local' : numericOrTemporal ? 'number' : undefined,
    valueEditorType: property.values?.length || isBoolean ? 'select' : 'text',
    values: property.values?.length ? property.values : isBoolean ? [{ name: 'true', label: 'True' }, { name: 'false', label: 'False' }] : undefined,
    operators: isArray ? arrayOperators : isBoolean ? booleanOperators : numericOrTemporal ? comparisonOperators : textOperators,
  };
}

function valueLabel(value: unknown): string {
  if (value === null || value === undefined || value === '') return '—';
  if (typeof value === 'object') return JSON.stringify(value);
  return String(value);
}

function queryChips(query: RuleGroupType): string[] {
  return query.rules.flatMap(node => {
    if (typeof node === 'string') return [];
    if ('rules' in node) return queryChips(node as RuleGroupType);
    const value = Array.isArray(node.value) ? node.value.join('…') : String(node.value ?? '');
    return [`${String(node.field)} ${String(node.operator)} ${value}`.trim()];
  });
}

export default function ObjectQueryTab({ ontologyId }: { ontologyId: string }) {
  const [params] = useSearchParams();
  return <ExplorerWorkspace key={`${ontologyId}:${params.toString()}`} ontologyId={ontologyId} />;
}

function ExplorerWorkspace({ ontologyId }: { ontologyId: string }) {
  const [searchParams, setSearchParams] = useSearchParams();
  const [restored] = useState(() => readExplorerUrl(searchParams));
  const [typeName, setTypeName] = useState(restored.objectType);
  const [mode, setMode] = useState<ExplorerMode>(restored.mode);
  const [draftQuery, setDraftQuery] = useState<RuleGroupType>(restored.query);
  const [appliedQuery, setAppliedQuery] = useState<RuleGroupType>(restored.query);
  const [pageTokens, setPageTokens] = useState<Array<string | null>>([null]);
  const [selectedIds, setSelectedIds] = useState<string[]>([]);
  const [previewId, setPreviewId] = useState<string | null>(null);
  const [showGraph, setShowGraph] = useState(false);
  const [showFilters, setShowFilters] = useState(restored.query.rules.length > 0);
  const [showColumns, setShowColumns] = useState(false);
  const [hiddenColumns, setHiddenColumns] = useState<string[]>([]);
  const [columnOrder, setColumnOrder] = useState<string[] | null>(null);
  const [columnWidths, setColumnWidths] = useState<Record<string, number>>({});
  const [sort, setSort] = useState<SortState>(null);
  const [chartProperties, setChartProperties] = useState<string[] | null>(null);
  const [chartWidths, setChartWidths] = useState<Record<string, 'half' | 'full'>>({});
  const [chartHistory, setChartHistory] = useState<string[][]>([]);
  const [chartRedo, setChartRedo] = useState<string[][]>([]);
  const [toast, setToast] = useState('');
  const viewId = searchParams.get('dataView') || '';
  const context: ExplorerContext = { ontology_id: ontologyId, consistency: viewId ? 'snapshot' : 'live', ...(viewId ? { data_view_id: viewId } : {}) };

  const entities = useQuery({
    queryKey: ['object-query-types', ontologyId],
    queryFn: ({ signal }) => apiClientV2.get<Catalog>(`/ontologies/${ontologyId}/object-query/catalog`, { signal }),
  });
  const types: Entity[] = useMemo(() => (entities.data?.types || []).filter(item => item.kind === 'object').map(item => ({ id: item.api_name, api_name: item.api_name, properties: item.properties.filter(property => property.searchable) })), [entities.data]);
  const selectedType = useMemo(() => types.find(entity => entityApiName(entity) === typeName) || types[0], [typeName, types]);
  const selected = selectedType ? entityApiName(selectedType) : '';
  const properties = useMemo(() => (selectedType?.properties || []).map(toExplorerProperty).filter((item): item is ExplorerProperty => item !== null), [selectedType]);
  const fields = useMemo(() => properties.map(queryBuilderField), [properties]);
  const compiled = useMemo(() => compileObjectSetExpression(selected, appliedQuery, properties), [appliedQuery, properties, selected]);
  const expressionState = useMemo(() => {
    const raw = searchParams.get('setExpression');
    if (!raw) return { expression: compiled.expression, error: '' };
    try {
      const base = JSON.parse(raw) as ObjectSetExpression;
      if (!base || typeof base !== 'object' || typeof base.kind !== 'string') throw new Error('invalid');
      return { expression: filterExpression(base, compiled.expression), error: '' };
    } catch { return { expression: compiled.expression, error: '集合 URL 无效，请清除筛选后重试' }; }
  }, [searchParams, compiled.expression]);
  const expression = expressionState.expression;
  const currentPage = pageTokens.length - 1;
  const pageToken = pageTokens[currentPage];

  const result = useQuery({
    queryKey: ['object-query-load', ontologyId, expression, context, pageToken],
    enabled: !!selected && entities.isSuccess && compiled.issues.length === 0 && !expressionState.error,
    queryFn: ({ signal }) => apiClientV2.post<LoadResult>(`/ontologies/${ontologyId}/object-query/load`, {
      expression,
      context,
      read: { page_size: 50, page_token: pageToken },
    }, { signal }),
  });
  const objects = result.data?.objects || EMPTY_OBJECTS;
  const availablePropertyNames = useMemo(() => {
    const metadataNames = properties.map(property => property.apiName);
    const observed = objects.flatMap(object => Object.keys(object.properties));
    return [...new Set([...metadataNames, ...observed])].slice(0, 12);
  }, [objects, properties]);
  const orderedPropertyNames = useMemo(() => {
    if (!columnOrder) return availablePropertyNames;
    return [...columnOrder.filter(name => availablePropertyNames.includes(name)), ...availablePropertyNames.filter(name => !columnOrder.includes(name))];
  }, [availablePropertyNames, columnOrder]);
  const visiblePropertyNames = orderedPropertyNames.filter(name => !hiddenColumns.includes(name)).slice(0, 8);
  const activeCharts = chartProperties ?? availablePropertyNames.slice(0, 4);
  const selectedObjects = objects.filter(object => selectedIds.includes(object.object_id));
  const preview = objects.find(object => object.object_id === previewId) || selectedObjects[0] || null;
  const displayObjects = useMemo(() => {
    if (!sort) return objects;
    return [...objects].sort((left, right) => {
      const leftValue = sort.property === 'object_id' ? left.object_id : left.properties[sort.property];
      const rightValue = sort.property === 'object_id' ? right.object_id : right.properties[sort.property];
      const comparison = String(leftValue ?? '').localeCompare(String(rightValue ?? ''), undefined, { numeric: true });
      return sort.direction === 'asc' ? comparison : -comparison;
    });
  }, [objects, sort]);

  const toggleSort = (property: string) => setSort(current => current?.property === property
    ? { property, direction: current.direction === 'asc' ? 'desc' : 'asc' }
    : { property, direction: 'asc' });
  const toggleSelection = useCallback((objectId: string) => setSelectedIds(ids => {
    const selectedNow = ids.includes(objectId);
    const next = selectedNow ? ids.filter(id => id !== objectId) : [...ids, objectId];
    if (!selectedNow) setPreviewId(objectId);
    else if (previewId === objectId) setPreviewId(next[0] || null);
    return next;
  }), [previewId]);
  const columns = useMemo(() => columnHelper.columns([
    columnHelper.display({
      id: 'select',
      header: '',
      cell: ({ row }) => <input type="checkbox" aria-label={`选择 ${row.original.object_id}`} checked={selectedIds.includes(row.original.object_id)} onChange={() => toggleSelection(row.original.object_id)} />,
    }),
    columnHelper.accessor(row => row.object_id, { id: 'object_id', header: () => <button type="button" className="oe-column-sort" onClick={() => toggleSort('object_id')}>Object {sort?.property === 'object_id' ? (sort.direction === 'asc' ? '↑' : '↓') : ''}</button>, cell: ({ row }) => <button type="button" className="oe-object-link" onClick={() => setPreviewId(row.original.object_id)}>{row.original.object_id}</button> }),
    ...visiblePropertyNames.map(name => columnHelper.accessor(row => row.properties[name], { id: name, header: () => <button type="button" className="oe-column-sort" onClick={() => toggleSort(name)}>{name} {sort?.property === name ? (sort.direction === 'asc' ? '↑' : '↓') : ''}</button>, cell: info => <span title={valueLabel(info.getValue())}>{valueLabel(info.getValue())}</span> })),
  ]), [selectedIds, sort, toggleSelection, visiblePropertyNames]);
  const table = useTable({ features: tableFeatureSet, data: displayObjects, columns });

  const apply = () => {
    const next = compileObjectSetExpression(selected, draftQuery, properties);
    if (next.issues.length) return;
    setAppliedQuery(draftQuery);
    setPageTokens([null]);
    setSelectedIds([]);
    setPreviewId(null);
    setShowFilters(false);
    setSearchParams(writeExplorerUrl(searchParams, { objectType: selected, mode, query: draftQuery }));
  };
  const switchMode = (nextMode: ExplorerMode) => {
    setMode(nextMode);
    setSearchParams(writeExplorerUrl(searchParams, { objectType: selected, mode: nextMode, query: appliedQuery }));
  };
  const switchType = (nextType: string) => {
    setTypeName(nextType);
    setDraftQuery(EMPTY_QUERY);
    setAppliedQuery(EMPTY_QUERY);
    setPageTokens([null]);
    setSelectedIds([]);
    setPreviewId(null);
    setColumnOrder(null);
    setHiddenColumns([]);
    setChartProperties(null);
    const next = writeExplorerUrl(searchParams, { objectType: nextType, mode, query: EMPTY_QUERY });
    next.delete('setExpression');
    setSearchParams(next);
  };
  const clearFilters = () => {
    setDraftQuery(EMPTY_QUERY);
    setAppliedQuery(EMPTY_QUERY);
    setPageTokens([null]);
    const next = writeExplorerUrl(searchParams, { objectType: selected, mode, query: EMPTY_QUERY });
    next.delete('setExpression');
    setSearchParams(next);
  };
  const changeExpression = (nextExpression: ObjectSetExpression, nextType: string) => {
    const next = writeExplorerUrl(searchParams, { objectType: nextType, mode, query: EMPTY_QUERY });
    next.set('setExpression', JSON.stringify(nextExpression));
    setSearchParams(next);
  };
  const commitCharts = (next: string[]) => {
    setChartHistory(history => [...history.slice(-9), activeCharts]);
    setChartRedo([]);
    setChartProperties(next);
  };
  const undoCharts = () => {
    const previous = chartHistory.at(-1);
    if (!previous) return;
    setChartRedo(items => [activeCharts, ...items].slice(0, 10));
    setChartHistory(items => items.slice(0, -1));
    setChartProperties(previous);
  };
  const redoCharts = () => {
    const next = chartRedo[0];
    if (!next) return;
    setChartHistory(items => [...items, activeCharts].slice(-10));
    setChartRedo(items => items.slice(1));
    setChartProperties(next);
  };
  const moveColumn = (name: string, delta: number) => {
    const order = [...orderedPropertyNames];
    const index = order.indexOf(name);
    const target = index + delta;
    if (index < 0 || target < 0 || target >= order.length) return;
    [order[index], order[target]] = [order[target], order[index]];
    setColumnOrder(order);
  };
  const notify = (message = '操作已完成。') => {
    setToast(message);
    window.setTimeout(() => setToast(''), 5000);
  };

  if (entities.isPending) return <div role="status" className="rounded border p-4 text-sm text-slate-500">正在加载对象类型</div>;
  if (entities.error) return <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">对象类型加载失败。<button onClick={() => void entities.refetch()}>重试</button></div>;
  if (!selected) return <div role="status">暂无可查询的对象类型。</div>;

  const draftIssues = compileObjectSetExpression(selected, draftQuery, properties).issues;
  const chips = queryChips(appliedQuery);
  const linkedTypes = (entities.data?.links || []).flatMap(link => [
    ...(link.source_type === selected ? [{ relation: link.api_name, target: link.target_type }] : []),
    ...(link.target_type === selected ? [{ relation: link.api_name, target: link.source_type }] : []),
  ]);

  return <section aria-label="Object Explorer" className="oe-shell" aria-busy={result.isFetching}>
    <header className="oe-command-bar">
      <div className="oe-type-mark">{(selectedType?.name_cn || selectedType?.name || selected).slice(0, 1).toUpperCase()}</div>
      <label className="oe-type-select"><span>Object type</span><select aria-label="对象类型" value={selected} onChange={event => switchType(event.target.value)}>{types.map(entity => <option key={entity.id} value={entityApiName(entity)}>{entity.name_cn || entity.name || entity.name_en || entity.id}</option>)}</select></label>
      <button type="button" className="oe-query-summary" aria-expanded={showFilters} onClick={() => setShowFilters(value => !value)}><Search size={15} /><span className="oe-query-placeholder">搜索 property、value 或 linked object</span>{chips.map((chip, index) => <span className="oe-filter-chip" key={`${chip}:${index}`}>{chip}</span>)}<ChevronDown size={14} /></button>
      {(compiled.activeRuleCount > 0 || searchParams.has('setExpression')) && <button type="button" className="oe-clear" onClick={clearFilters}><X size={13} />Clear</button>}
    </header>

    {showFilters && <section className="oe-filter-popover" aria-label="筛选器">
      <aside className="oe-filter-catalog"><label><Search size={13} /><input aria-label="搜索筛选字段" placeholder="搜索字段" /></label><h3>Properties</h3>{properties.map(property => <button type="button" key={property.apiName} onClick={() => setDraftQuery(query => ({ ...query, rules: [...query.rules, { field: property.apiName, operator: '=', value: '' }] }))}><span>{property.label}</span><small>{property.dataType}</small></button>)}<h3>Linked object types</h3>{linkedTypes.map(item => <div className="oe-linked-option" key={`${item.relation}:${item.target}`}><span>{item.target}</span><small>{item.relation}</small></div>)}</aside>
      <div className="oe-filter-editor"><div className="oe-filter-title"><Filter size={15} /><div><strong>Filter current set</strong><span>支持嵌套 AND / OR / NOT</span></div></div>{fields.length ? <div className="oe-query-builder"><QueryBuilder fields={fields} query={draftQuery} onQueryChange={setDraftQuery} showCombinatorsBetweenRules showNotToggle /></div> : <p className="oe-empty-copy">该 Object Type 没有已发布的 property metadata。</p>}<div className="oe-filter-footer"><div>{draftIssues.length ? <span role="alert" className="text-red-600">{draftIssues[0]}</span> : <span>筛选在 Apply 后执行并写入可恢复 URL。</span>}</div><button type="button" onClick={apply} disabled={draftIssues.length > 0}>Apply filters</button></div></div>
    </section>}

    {entities.data && <ExplorerOperations ontologyId={ontologyId} expression={expression} objectType={selected} selectedIds={selectedIds} context={context} catalog={entities.data} onExpression={changeExpression} onView={id => { const next = new URLSearchParams(searchParams); if (id) next.set('dataView', id); else next.delete('dataView'); setSearchParams(next); }} />}
    <ExplorerAnalysis ontologyId={ontologyId} expression={expression} context={context} properties={entities.data?.types.find(item => item.api_name === selected)?.properties.filter(item => item.aggregatable).map(item => item.api_name) ?? []} onFilter={(property, value) => changeExpression({ kind: 'filter', input: expression, where: value === null ? { kind: 'null_test', property: { api_name: property }, is_null: true } : { kind: 'comparison', property: { api_name: property }, op: 'eq', value } }, selected)} />

    <div className="oe-workspace-tabs" role="tablist">
      <div className="oe-layout-tools"><button type="button" onClick={undoCharts} disabled={!chartHistory.length} aria-label="撤销图表布局"><RotateCcw size={14} /></button><button type="button" onClick={redoCharts} disabled={!chartRedo.length} aria-label="重做图表布局"><RotateCcw size={14} className="oe-redo-icon" /></button><span><LayoutGrid size={14} />自定义布局</span></div>
      <button type="button" role="tab" aria-selected={mode === 'explore'} onClick={() => switchMode('explore')} className={mode === 'explore' ? 'active' : ''}><BarChart3 size={14} />Explore</button>
      <button type="button" role="tab" aria-selected={mode === 'results'} onClick={() => switchMode('results')} className={mode === 'results' ? 'active' : ''}><List size={14} />Results</button>
      <span className="oe-result-meta">{result.data?.page.returned ?? 0} visible · {result.data?.completeness || 'loading'}</span>
      <div className="oe-workspace-actions"><button type="button" aria-label="对所选对象执行 Action" disabled={!selectedIds.length} onClick={() => setPreviewId(selectedIds[0])}>Actions</button><button type="button" onClick={() => setShowGraph(value => !value)}>{showGraph ? '关闭关系图' : 'Open in Graph'}</button>{mode === 'results' && <button type="button" aria-expanded={showColumns} onClick={() => setShowColumns(value => !value)}><Columns3 size={14} />Columns</button>}<button type="button" className="oe-refresh" disabled={result.isFetching} onClick={() => void result.refetch()} aria-label={result.isFetching ? '正在刷新对象集合' : '刷新对象集合'}><RefreshCw size={14} /></button></div>
    </div>

    {showColumns && <section className="oe-column-panel" aria-label="配置结果列"><header><div><Settings2 size={15} /><strong>配置结果列</strong></div><button type="button" aria-label="关闭列配置" onClick={() => setShowColumns(false)}><X size={15} /></button></header>{orderedPropertyNames.map((name, index) => <div className="oe-column-option" key={name}><GripVertical size={13} /><label><input type="checkbox" checked={!hiddenColumns.includes(name)} onChange={() => setHiddenColumns(items => items.includes(name) ? items.filter(item => item !== name) : [...items, name])} />{name}</label><button type="button" aria-label={`上移 ${name}`} disabled={index === 0} onClick={() => moveColumn(name, -1)}><ChevronUp size={13} /></button><button type="button" aria-label={`下移 ${name}`} disabled={index === orderedPropertyNames.length - 1} onClick={() => moveColumn(name, 1)}><ChevronDown size={13} /></button><label className="oe-width-control">宽度<input type="range" min="100" max="320" value={columnWidths[name] || 180} onChange={event => setColumnWidths(widths => ({ ...widths, [name]: Number(event.target.value) }))} /></label></div>)}</section>}

    {showGraph && <Suspense fallback={<p role="status">正在加载关系图…</p>}><ObjectGraph ontologyId={ontologyId} expression={expression} context={context} /></Suspense>}
    {result.isLoading && <div role="status" className="oe-state">正在读取对象集合…</div>}
    {result.isFetching && !result.isLoading && <p role="status" className="oe-refresh-status">正在刷新对象集合…</p>}
    {result.error && <div role="alert" className="oe-state oe-state--error">{explorerError(result.error)}<button onClick={() => void result.refetch()}>重试</button></div>}
    {expressionState.error && <div role="alert">{expressionState.error}</div>}
    {compiled.issues.length > 0 && <div role="alert" className="oe-state oe-state--error">{compiled.issues.join('；')}</div>}

    {result.data && <div className={`oe-content ${preview ? 'has-preview' : ''}`}>
      <main className="oe-main">
        {mode === 'explore' ? <div className="oe-explore-layout">
          <div className="oe-chart-grid">{activeCharts.map((property, index) => <article className={`oe-chart-card ${chartWidths[property] === 'full' ? 'is-full' : ''}`} key={property}>
            <div className="oe-chart-card__header"><div><span>当前页分布</span><strong>{property}</strong></div><div className="oe-chart-card__controls"><button type="button" aria-label={`${property} 左移`} disabled={index === 0} onClick={() => { const next = [...activeCharts]; [next[index - 1], next[index]] = [next[index], next[index - 1]]; commitCharts(next); }}><ChevronLeft size={13} /></button><button type="button" aria-label={`${property} 右移`} disabled={index === activeCharts.length - 1} onClick={() => { const next = [...activeCharts]; [next[index + 1], next[index]] = [next[index], next[index + 1]]; commitCharts(next); }}><ChevronRight size={13} /></button><button type="button" aria-label={`${property} ${chartWidths[property] === 'full' ? '缩小' : '放大'}`} onClick={() => setChartWidths(widths => ({ ...widths, [property]: widths[property] === 'full' ? 'half' : 'full' }))}>{chartWidths[property] === 'full' ? <Minimize2 size={13} /> : <Maximize2 size={13} />}</button><button type="button" aria-label={`删除 ${property} 图表`} onClick={() => commitCharts(activeCharts.filter(item => item !== property))}><Trash2 size={13} /></button></div></div>
            <Suspense fallback={<div className="oe-chart-empty">正在加载图表渲染器…</div>}><ObjectDistributionChart objects={objects} property={property} /></Suspense><p className="oe-chart-note">当前返回页 · 完整统计使用 pinned data view</p>
          </article>)}<label className="oe-add-chart"><Plus size={20} /><strong>Add chart</strong><select aria-label="添加图表属性" value="" onChange={event => { if (event.target.value) commitCharts([...activeCharts, event.target.value]); }}><option value="">选择 property</option>{availablePropertyNames.filter(name => !activeCharts.includes(name)).map(name => <option key={name}>{name}</option>)}</select></label></div>
          <aside className="oe-results-rail"><header><strong>Results</strong><span>{objects.length}</span></header>{objects.slice(0, 12).map(object => <button type="button" key={object.object_id} onClick={() => setPreviewId(object.object_id)}><span className="oe-result-icon">{object.object_type.slice(0, 1)}</span><span><strong>{object.object_id}</strong><small>{visiblePropertyNames.slice(0, 2).map(name => valueLabel(object.properties[name])).join(' · ')}</small></span></button>)}</aside>
        </div> : <div className="oe-table-wrap"><table className="oe-results-table"><thead>{table.getHeaderGroups().map(group => <tr key={group.id}>{group.headers.map(header => <th key={header.id} style={header.id in columnWidths ? { width: columnWidths[header.id], minWidth: columnWidths[header.id] } : undefined}>{header.isPlaceholder ? null : <table.FlexRender header={header} />}</th>)}</tr>)}</thead><tbody>{table.getRowModel().rows.map(row => <tr key={row.id} className={selectedIds.includes(row.original.object_id) ? 'selected' : ''}>{row.getAllCells().map(cell => <td key={cell.id}><table.FlexRender cell={cell} /></td>)}</tr>)}</tbody></table>{!objects.length && <div className="oe-state">当前条件没有对象。</div>}</div>}
        <footer className="oe-pagination"><span>{selectedIds.length} selected · 状态 {result.data.status} · {result.data.page.stability || 'live'}</span><div><button type="button" disabled={currentPage === 0} onClick={() => { setPageTokens(tokens => tokens.slice(0, -1)); setPreviewId(null); }}><ChevronLeft size={14} />上一页</button><span>第 {currentPage + 1} 页</span><button type="button" disabled={!result.data.page.has_more || !result.data.page.next_page_token} onClick={() => { setPageTokens(tokens => [...tokens, result.data.page.next_page_token || null]); setPreviewId(null); }}>下一页<ChevronRight size={14} /></button></div></footer>
      </main>
      {preview && <aside className="oe-selection-preview" aria-label="Selection Preview">{selectedObjects.length > 0 && <div className="oe-selection-cards"><header><strong>Selection Preview</strong><span>{selectedObjects.length}</span></header><div>{selectedObjects.slice(0, 20).map(object => <button type="button" key={object.object_id} className={preview.object_id === object.object_id ? 'active' : ''} onClick={() => setPreviewId(object.object_id)}><span>{object.object_type.slice(0, 1)}</span><strong>{object.object_id}</strong></button>)}</div></div>}<ObjectPanel object={preview} onClose={() => { setPreviewId(null); setSelectedIds([]); }} onOpenGraph={() => setShowGraph(true)} ontologyId={ontologyId} dataViewId={context.data_view_id} onActionCommitted={message => { void result.refetch(); notify(message); }} /></aside>}
    </div>}
    {toast && <div className="oe-toast" role="status"><span>✓</span><div><strong>操作成功</strong><p>{toast}</p></div><button type="button" aria-label="关闭成功提示" onClick={() => setToast('')}><X size={15} /></button></div>}
  </section>;
}
