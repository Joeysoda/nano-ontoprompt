import { useMemo, useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { ChevronDown, Download, GitBranch, Layers3, LockKeyhole, Save, Share2, X } from 'lucide-react';
import { apiClientV2 } from '@/api/client';
import { setArithmetic, type ObjectSetExpression } from './contract';
import { explorerError, loadComplete } from './runtime';

export type ExplorerContext = { ontology_id: string; consistency: 'live' | 'snapshot'; data_view_id?: string };
export type Catalog = {
  metadata_digest: string;
  types: Array<{ api_name: string; kind: string; properties: Array<{ api_name: string; data_type: string; searchable: boolean; aggregatable: boolean }> }>;
  links: Array<{ api_name: string; source_type: string; target_type: string }>;
};
type Resource = { id: string; name: string; head_version: number; etag: number; status: string; expired: boolean; definition_kind: string };
type SaveKind = 'static' | 'dynamic';

function baseType(expression: ObjectSetExpression): string | null {
  if (expression.kind === 'base' || expression.kind === 'static') return expression.type_ref.api_name;
  if (expression.kind === 'filter' || expression.kind === 'traverse') return baseType(expression.input);
  if (expression.kind === 'subtract') return baseType(expression.base);
  if (expression.kind === 'union' || expression.kind === 'intersect') return expression.inputs.length ? baseType(expression.inputs[0]) : null;
  return null;
}

function traversals(expression: ObjectSetExpression): Array<{ api_name: string; direction: 'in' | 'out' }> {
  if (expression.kind === 'traverse') return [...traversals(expression.input), expression.link];
  if (expression.kind === 'filter') return traversals(expression.input);
  if (expression.kind === 'subtract') return traversals(expression.base);
  if (expression.kind === 'union' || expression.kind === 'intersect') return expression.inputs.flatMap(traversals);
  return [];
}

export default function ExplorerOperations({ ontologyId, expression, objectType, selectedIds, context, catalog, onExpression, onView }: {
  ontologyId: string; expression: ObjectSetExpression; objectType: string; selectedIds: string[];
  context: ExplorerContext; catalog: Catalog; onExpression: (expression: ObjectSetExpression, type: string) => void; onView: (id: string) => void;
}) {
  const queryClient = useQueryClient();
  const [name, setName] = useState('');
  const [description, setDescription] = useState('');
  const [visibility, setVisibility] = useState<'private' | 'public'>('private');
  const [location, setLocation] = useState('My workspace');
  const [saveKind, setSaveKind] = useState<SaveKind | null>(null);
  const [resourceId, setResourceId] = useState('');
  const [principal, setPrincipal] = useState('');
  const [notice, setNotice] = useState('');
  const [showResources, setShowResources] = useState(false);
  const root = `/ontologies/${ontologyId}`;
  const resources = useQuery({ queryKey: ['explorer-resources', ontologyId], queryFn: ({ signal }) => apiClientV2.get<{ resources: Resource[] }>(`${root}/object-sets/resources`, { signal }) });
  const active = resources.data?.resources.filter(item => item.status === 'active' && !item.expired) ?? [];
  const resource = active.find(item => item.id === resourceId);
  const snapshot = useMutation({ mutationFn: () => apiClientV2.post<{ view_id: string; status: string }>(`${root}/object-query/data-views`, {
    source_ontology_id: ontologyId, source_manifest_digest: catalog.metadata_digest,
  }), onSuccess: result => { if (result.status === 'ready') onView(result.view_id); else setNotice(`视图状态：${result.status}`); } });
  const save = useMutation({ mutationFn: async (kind: SaveKind) => {
    let savedExpression = expression;
    if (kind === 'static') {
      const ids = selectedIds.length ? selectedIds : (await loadComplete(ontologyId, expression, context)).map(item => item.object_id);
      savedExpression = { kind: 'static', type_ref: { kind: 'object', api_name: objectType }, object_ids: ids };
    }
    return apiClientV2.post<Resource>(`${root}/object-sets/resources`, { name: name.trim(), description: description.trim(), definition: { expression: savedExpression } });
  }, onSuccess: async result => {
    setNotice(`已保存 ${result.name}`);
    setResourceId(result.id);
    setSaveKind(null);
    setName('');
    setDescription('');
    await queryClient.invalidateQueries({ queryKey: ['explorer-resources', ontologyId] });
  } });
  const open = useMutation({ mutationFn: async (operation: 'open' | 'union' | 'intersect' | 'subtract') => {
    if (!resource) return;
    const reference: ObjectSetExpression = { kind: 'reference', object_set_id: resource.id, definition_version: resource.head_version };
    const next = operation === 'open' ? reference : setArithmetic(operation, expression, reference);
    const validated = await apiClientV2.post<{ result_type: { api_name: string } }>(`${root}/object-sets/validate`, { expression: next });
    onExpression(next, validated.result_type.api_name);
  } });
  const share = useMutation({ mutationFn: () => apiClientV2.put(`${root}/object-sets/resources/${resourceId}/grants`, {
    expected_etag: resource?.etag, principal_id: principal.trim(), role: 'view',
  }), onSuccess: async () => { setNotice('已授予集合查看权限；底层数据权限保持独立'); await queryClient.invalidateQueries({ queryKey: ['explorer-resources', ontologyId] }); } });
  const exportData = useMutation({ mutationFn: async () => {
    const rows = await loadComplete(ontologyId, expression, context);
    const url = URL.createObjectURL(new Blob([JSON.stringify({ context, expression, objects: rows }, null, 2)], { type: 'application/json' }));
    const anchor = document.createElement('a'); anchor.href = url; anchor.download = `${objectType}.json`; anchor.click();
    setTimeout(() => URL.revokeObjectURL(url), 1000); setNotice(`已导出 ${rows.length} 个对象`);
  } });
  const busy = save.isPending || open.isPending || share.isPending || snapshot.isPending || exportData.isPending;
  const error = save.error || open.error || share.error || snapshot.error || exportData.error || resources.error;
  const links = catalog.links.flatMap(link => [
    ...(link.source_type === objectType ? [{ ...link, direction: 'out' as const, target: link.target_type }] : []),
    ...(link.target_type === objectType ? [{ ...link, direction: 'in' as const, target: link.source_type }] : []),
  ]);
  const path = useMemo(() => {
    const origin = baseType(expression) || objectType;
    return traversals(expression).reduce<{ current: string; items: Array<{ source: string; relation: string; direction: 'in' | 'out'; target: string }> }>((state, step) => {
      const link = catalog.links.find(item => item.api_name === step.api_name);
      const next = step.direction === 'out' ? link?.target_type : link?.source_type;
      const item = { source: state.current, relation: step.api_name, direction: step.direction, target: next || '?' };
      return { current: item.target, items: [...state.items, item] };
    }, { current: origin, items: [] }).items;
  }, [catalog.links, expression, objectType]);

  return <>
    <div className="oe-resource-bar">
      <div className="oe-resource-context"><span className={context.data_view_id ? 'is-pinned' : 'is-live'} /> <span role="status">{context.data_view_id ? `固定视图 · ${context.data_view_id}` : 'Live · 实时数据'}</span></div>
      <div className="oe-resource-actions">
        <button type="button" disabled={busy} onClick={() => snapshot.mutate()}><LockKeyhole size={14} />固定当前数据</button>
        {context.data_view_id && <button type="button" onClick={() => onView('')}>返回 Live</button>}
        <button type="button" disabled={busy || (!selectedIds.length && !context.data_view_id)} onClick={() => setSaveKind('static')}><Save size={14} />保存列表{selectedIds.length ? `（选中 ${selectedIds.length}）` : '（完整集合）'}</button>
        <button type="button" disabled={busy} onClick={() => setSaveKind('dynamic')}><Save size={14} />保存动态查询</button>
        <button type="button" disabled={busy || !context.data_view_id} onClick={() => exportData.mutate()}><Download size={14} />导出</button>
        <button type="button" aria-expanded={showResources} onClick={() => setShowResources(value => !value)}><Layers3 size={14} />集合工具<ChevronDown size={13} /></button>
      </div>
    </div>

    <div className="oe-traversal-bar">
      <div className="oe-traversal-path" aria-label="Traversal path">
        <span className="oe-path-chip oe-path-chip--type">{path[0]?.source || objectType}</span>
        {path.map((step, index) => <span className="oe-path-step" key={`${step.relation}:${index}`}><span aria-hidden="true">{step.direction === 'out' ? '→' : '←'}</span><span className="oe-path-chip oe-path-chip--link"><GitBranch size={12} />{step.relation}</span><span aria-hidden="true">→</span><span className="oe-path-chip oe-path-chip--type">{step.target}</span></span>)}
      </div>
      <label className="oe-search-around"><span>Linked objects</span><select aria-label="Search Around" value="" onChange={event => {
        const link = links[Number(event.target.value)];
        if (link) onExpression({ kind: 'traverse', input: expression, link: { api_name: link.api_name, direction: link.direction } }, link.target);
      }}><option value="">Search Around</option>{links.map((link, index) => <option key={`${link.api_name}:${link.direction}:${link.target}`} value={index}>{objectType} {link.direction === 'out' ? '→' : '←'} {link.api_name} · {link.target}</option>)}</select></label>
    </div>

    {showResources && <section className="oe-resource-drawer" aria-label="集合工具">
      <label>已保存集合<select aria-label="已保存集合" value={resourceId} onChange={event => setResourceId(event.target.value)}><option value="">选择已保存集合</option>{active.map(item => <option key={item.id} value={item.id}>{item.name} · v{item.head_version}</option>)}</select></label>
      <div className="oe-resource-drawer__buttons">{(['open', 'union', 'intersect', 'subtract'] as const).map((operation, index) => <button key={operation} disabled={!resource || busy} onClick={() => open.mutate(operation)}>{['打开', '并集', '交集', '差集'][index]}</button>)}</div>
      <label>共享用户 ID<input aria-label="共享用户 ID" placeholder="用户或组 ID" value={principal} onChange={event => setPrincipal(event.target.value)} /></label>
      <button disabled={!resource || !principal.trim() || busy} onClick={() => share.mutate()}><Share2 size={14} />共享查看权限</button>
    </section>}

    {busy && <p role="status" className="oe-operation-status">正在处理…</p>}
    {notice && <p role="status" className="oe-operation-status oe-operation-status--success">{notice}</p>}
    {error && <p role="alert" className="oe-operation-status oe-operation-status--error">{error instanceof Error ? error.message : explorerError(error)}</p>}

    {saveKind && <div className="oe-modal-backdrop" role="presentation" onMouseDown={event => { if (event.target === event.currentTarget) setSaveKind(null); }}>
      <section role="dialog" aria-modal="true" aria-label={saveKind === 'static' ? '保存列表' : '保存动态查询'} className="oe-save-dialog">
        <header><div><p className="oe-kicker">保存资源</p><h2>{saveKind === 'static' ? '保存列表' : '保存动态查询'}</h2></div><button type="button" aria-label="关闭保存弹窗" onClick={() => setSaveKind(null)}><X size={17} /></button></header>
        <p className="oe-save-dialog__description">{saveKind === 'static' ? `固定 ${selectedIds.length ? `${selectedIds.length} 个选中对象` : '完整集合成员'}，后续数据变化不会改变成员。` : '保存当前查询、筛选、关系路径与视图定义，重新打开时计算最新成员。'}</p>
        <label>名称 *<input autoFocus aria-label="集合名称" value={name} onChange={event => setName(event.target.value)} maxLength={200} placeholder={saveKind === 'static' ? '例如：Q4 重点订单' : '例如：待审批订单探索'} /></label>
        <label>说明<textarea aria-label="集合说明" value={description} onChange={event => setDescription(event.target.value)} placeholder="说明用途和维护人" /></label>
        <fieldset><legend>访问范围</legend><label><input type="radio" name="visibility" checked={visibility === 'private'} onChange={() => setVisibility('private')} />Private</label><label><input type="radio" name="visibility" checked={visibility === 'public'} onChange={() => setVisibility('public')} />Public</label></fieldset>
        <label>保存位置<input aria-label="保存位置" value={location} disabled={visibility === 'private'} onChange={event => setLocation(event.target.value)} /></label>
        <footer><button type="button" onClick={() => setSaveKind(null)}>取消</button><button type="button" className="primary" disabled={busy || !name.trim()} onClick={() => save.mutate(saveKind)}>确认保存 {saveKind === 'static' ? '列表' : '动态查询'}</button></footer>
      </section>
    </div>}
  </>;
}
