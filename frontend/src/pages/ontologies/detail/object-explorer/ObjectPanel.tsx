import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { X } from "lucide-react";
import { apiClientV2 } from "@/api/client";

export type ObjectPanelRecord = {
  object_id: string;
  object_type: string;
  properties: Record<string, unknown>;
};

const TimeSeriesChart = lazy(() => import('./TimeSeriesChart'));
const ObjectActionRunner = lazy(() => import('./ObjectActionRunner'));
type ViewSection = { id: string; title: string; properties: string[]; layout: 'list' | 'grid'; show_in_panel: boolean; visible_when?: { property: string; equals: unknown } | null };
type ViewConfig = { tabs: ViewSection[] };
type ViewResponse = { configured: ViewConfig | null; configured_version: number | null };

function visibleTabs(config: ViewConfig | null, object: ObjectPanelRecord, panel: boolean): ViewSection[] {
  return (config?.tabs || []).filter(tab => (!panel || tab.show_in_panel) &&
    (!tab.visible_when || object.properties[tab.visible_when.property] === tab.visible_when.equals));
}

function Properties({ object, names, grid = false, onSeries }: { object: ObjectPanelRecord; names: string[]; grid?: boolean; onSeries?: (id: string) => void }) {
  return <dl className={`oe-property-list ${grid ? 'grid grid-cols-2 gap-x-4' : ''}`}>
    {names.filter(name => name in object.properties).map(name => <div key={name}><dt>{name}</dt><dd>{displayValue(object.properties[name])}{onSeries && typeof object.properties[name] === 'string' && object.properties[name].startsWith('series:') && <button className="ml-2 text-blue-700" onClick={() => onSeries(object.properties[name] as string)}>查看曲线</button>}</dd></div>)}
  </dl>;
}

function displayValue(value: unknown): string {
  if (value === null || value === undefined || value === "") return "—";
  if (typeof value === "object") return JSON.stringify(value);
  return String(value);
}

export default function ObjectPanel({
  object,
  onClose,
  children,
  className = "",
  showEmptyState = true,
  testId,
  onOpenGraph,
  ontologyId,
  dataViewId,
  onActionCommitted,
  readOnly = false,
}: {
  object: ObjectPanelRecord;
  onClose: () => void;
  children?: ReactNode;
  className?: string;
  showEmptyState?: boolean;
  testId?: string;
  onOpenGraph?: () => void;
  ontologyId?: string;
  dataViewId?: string;
  onActionCommitted?: (message?: string) => void;
  readOnly?: boolean;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  const [seriesId, setSeriesId] = useState<string | null>(null);
  const [showActions, setShowActions] = useState(false);
  const [configured, setConfigured] = useState<ViewConfig | null>(null);
  const [viewVersion, setViewVersion] = useState<number | null>(null);
  const [useConfigured, setUseConfigured] = useState(true);
  const [activeTab, setActiveTab] = useState<string | null>(null);
  useEffect(() => {
    if (!ontologyId) return;
    let active = true;
    apiClientV2.get<ViewResponse>(`/ontologies/${ontologyId}/object-views/${encodeURIComponent(object.object_type)}`).then(response => {
      if (active) { setConfigured(response.configured); setViewVersion(response.configured_version); }
    }).catch(() => { if (active) { setConfigured(null); setViewVersion(null); } });
    return () => { active = false; };
  }, [ontologyId, object.object_type]);
  const panelTabs = visibleTabs(configured, object, true);
  const fullTabs = visibleTabs(configured, object, false);
  const selectedTab = fullTabs.find(tab => tab.id === activeTab) || fullTabs[0];
  return (
    <aside className={`oe-object-panel ${className}`.trim()} aria-label={`${object.object_id} 对象预览`} data-testid={testId}>
      <div className="oe-object-panel__header">
        <div className="min-w-0">
          <p className="oe-kicker">{object.object_type}</p>
          <h3 className="truncate">{object.object_id}</h3>
        </div>
        <button type="button" onClick={onClose} aria-label="关闭对象预览"><X size={16} /></button>
      </div>
      <div className="oe-object-panel__actions">
        <button type="button" disabled={!ontologyId || !!dataViewId || readOnly} title={dataViewId || readOnly ? '当前上下文不能写入 live' : undefined} onClick={() => setShowActions(true)}>Actions</button>
        {onOpenGraph && <button type="button" onClick={onOpenGraph}>Open in Graph</button>}
        <button type="button" onClick={() => dialog.current?.showModal()}>完整对象视图</button>
      </div>
      {useConfigured && panelTabs.length ? panelTabs.map(tab => <div key={tab.id}><p className="px-3 pt-3 text-xs font-medium text-slate-500">{tab.title}</p><Properties object={object} names={tab.properties} grid={tab.layout === 'grid'} onSeries={ontologyId && dataViewId ? setSeriesId : undefined} /></div>) : <Properties object={object} names={Object.keys(object.properties)} onSeries={ontologyId && dataViewId ? setSeriesId : undefined} />}
      {showEmptyState && !Object.keys(object.properties).length && <p className="oe-empty-copy">该对象没有可显示的授权属性。</p>}
      {children}
      {seriesId && ontologyId && dataViewId && <Suspense fallback={<p role="status">正在加载曲线…</p>}><TimeSeriesChart ontologyId={ontologyId} dataViewId={dataViewId} seriesId={seriesId} /></Suspense>}
      {showActions && ontologyId && <Suspense fallback={<p role="status">正在加载 Action…</p>}><ObjectActionRunner ontologyId={ontologyId} object={object} onClose={() => setShowActions(false)} onCommitted={onActionCommitted} /></Suspense>}
      <dialog ref={dialog} className="m-auto max-h-[90vh] w-[min(900px,95vw)] overflow-auto rounded border p-6" aria-label={`${object.object_id} 完整对象视图`}>
        <header className="flex items-center justify-between"><h2>{object.object_type} · {object.object_id}</h2><button onClick={() => dialog.current?.close()} autoFocus>关闭完整视图</button></header>
        {configured && <div className="mt-3 flex items-center gap-2"><button type="button" aria-pressed={!useConfigured} onClick={() => setUseConfigured(false)}>Standard</button><button type="button" aria-pressed={useConfigured} onClick={() => setUseConfigured(true)}>Configured v{viewVersion}</button></div>}
        {useConfigured && selectedTab ? <><nav aria-label="对象视图标签" className="mt-3 flex gap-2">{fullTabs.map(tab => <button type="button" key={tab.id} aria-current={selectedTab.id === tab.id ? 'page' : undefined} onClick={() => setActiveTab(tab.id)}>{tab.title}</button>)}</nav><Properties object={object} names={selectedTab.properties} grid={selectedTab.layout === 'grid'} /></> : <Properties object={object} names={Object.keys(object.properties)} />}
        {children}
      </dialog>
    </aside>
  );
}
