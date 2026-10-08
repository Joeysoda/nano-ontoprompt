import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { apiClientV2 } from '@/api/client';

type Node = { id: string; entity_type: string; properties: Record<string, unknown> };
type Edge = { source: string; target: string; type: string; properties: { source_field: string } };
type Report = {
  available: boolean; import_status: string; error?: string; scope: string; source_url: string;
  source_records: number; excluded_counts: number; counts: Record<string, number>; baseline_date: string | null;
  demand_statuses: Record<string, number>; nodes: Node[]; edges: Edge[];
  limitations: string[]; type_labels: Record<string, string>;
  readiness: { question: string; missing: string }[];
  field_semantics: Record<string, { label: string; unit?: string; description?: string }>;
  selection?: { nodes: Node[]; edges: Edge[]; scope: string };
  evidence: { source_file: string; source_row_id: string; content_hash: string; text: string }[];
};

export default function ManufacturingDataTab({ ontologyId }: { ontologyId: string }) {
  const [selected, setSelected] = useState('');
  const [filter, setFilter] = useState('Demand');
  const [search, setSearch] = useState('');
  const { data, error, isPending, refetch } = useQuery<Report>({
    queryKey: ['manufacturing-data', ontologyId, selected],
    queryFn: () => apiClientV2.get(`/ontologies/${ontologyId}/manufacturing-data`, {
      params: selected ? { object_id: selected } : {},
    }),
  });
  if (isPending) return <p role="status">正在加载业务数据…</p>;
  if (error) return <div role="alert">加载失败：{error instanceof Error ? error.message : JSON.stringify(error)}<button onClick={() => { void refetch() }} className="ml-3 underline">重新加载</button><button onClick={() => setSelected('')} className="ml-3 underline">返回全部对象</button></div>;
  if (!data?.available) return <p>当前本体没有 frePPLe 业务数据导入记录。不会用演示数据覆盖已有本体。</p>;
  const node = data.nodes.find(n => n.id === selected);
  const names = new Map(data.nodes.map(n => [n.id, String(n.properties.name)]));
  const visible = data.nodes.filter(n => (!filter || n.entity_type === filter) &&
    String(n.properties.name).toLowerCase().includes(search.toLowerCase()));
  const direct = data.edges.filter(e => e.source === selected || e.target === selected);
  return <div className="space-y-5">
    <section className="rounded-xl border bg-white p-5 space-y-2">
      <h2 className="text-xl font-semibold">制造业务数据就绪检查</h2>
      <p>官方桌椅制造样例 · 不是实际工厂数据 · 未运行排程或 What-if</p>
      <p>{data.scope}</p>
      <p data-testid="manufacturing-counts">源记录 {data.source_records} · 业务对象 {data.nodes.length} · 关系 {data.edges.length} · 配置记录 {data.excluded_counts}（保留在导入记录，不映射为业务对象）</p>
      <p>需求：待处理 {data.demand_statuses.open || 0}，已关闭历史 {data.demand_statuses.closed || 0}</p>
      <p>样例计划基准时间：{data.baseline_date || '未知'}（原始配置，未平移到今天）</p>
      <p role="status">导入状态：{data.import_status}{data.error && ` — ${data.error}`}</p>
      <a className="text-blue-700 underline" href={data.source_url} target="_blank" rel="noreferrer">查看固定版本官方来源</a>
    </section>
    <section className="rounded-xl border border-amber-300 bg-amber-50 p-5">
      <h3 className="font-semibold">还不能直接计算什么？</h3>
      {data.readiness.map(r => <p className="mt-2" key={r.question}><strong>{r.question}</strong>：{r.missing}</p>)}
      <details className="mt-3"><summary>数据语义与已知限制</summary>{data.limitations.map(t => <p key={t}>{t}</p>)}</details>
    </section>
    <div className="grid gap-5 lg:grid-cols-2">
      <section className="rounded-xl border bg-white p-5 space-y-3">
        <h3 className="font-semibold">浏览业务对象</h3>
        <select aria-label="对象类型" className="w-full border p-2" value={filter} onChange={e => setFilter(e.target.value)}>
          {Object.entries(data.counts).map(([kind, count]) => <option key={kind} value={kind}>{data.type_labels[kind]}（{count}）</option>)}
        </select>
        <input aria-label="搜索业务对象" className="w-full border p-2" placeholder="按名称搜索，如 Demand 01" value={search} onChange={e => setSearch(e.target.value)} />
        <p>匹配 {visible.length} 个对象；列表可滚动，数据未截断。</p>
        <div className="max-h-96 overflow-auto space-y-1">{visible.map(n => <button key={n.id}
          className={`block w-full text-left border rounded p-2 ${selected === n.id ? 'bg-purple-50 border-purple-500' : ''}`}
          onClick={() => setSelected(n.id)}>{String(n.properties.name)} {n.properties.status === 'closed' ? '· 历史已关闭' : n.properties.status === 'open' ? '· 待处理' : ''}</button>)}</div>
      </section>
      <section className="rounded-xl border bg-white p-5 space-y-3 min-w-0">
        <h3 className="font-semibold">{node ? String(node.properties.name) : '选择对象查看业务含义与来源'}</h3>
        {node && <>
          <p>{data.type_labels[node.entity_type]} · 官方样例原始记录</p>
          <dl className="max-h-80 overflow-auto">{Object.entries(node.properties).filter(([k]) => !k.startsWith('source_')).map(([k,v]) =>
            <div key={k} className="border-b py-2 break-words"><dt className="font-medium">{data.field_semantics[k]?.label || k}{data.field_semantics[k]?.unit ? `（${data.field_semantics[k].unit}）` : ''}</dt>
              <dd>{v === null ? '未知 / 源数据未提供' : String(v)}</dd><dd className="text-sm text-slate-500">{data.field_semantics[k]?.description}</dd></div>)}</dl>
          <h4 className="font-semibold">直接关联（不是因果判断）</h4>
          <div className="max-h-56 overflow-auto">{direct.map((e,i) => <p key={i} className="py-1 text-sm">{names.get(e.source)} → {data.field_semantics[e.properties.source_field]?.label || e.properties.source_field} → <button className="text-blue-700 underline" onClick={() => setSelected(e.source === selected ? e.target : e.source)}>{names.get(e.target)}{e.target === selected ? `（查看 ${names.get(e.source)}）` : ''}</button></p>)}</div>
          <h4 className="font-semibold">来源证据</h4>
          {data.evidence.map(e => <details key={e.content_hash}><summary>{e.source_row_id} · 查看原始记录</summary><pre className="text-xs whitespace-pre-wrap break-all">{e.text}</pre><p className="break-all text-xs">SHA-256：{e.content_hash}</p></details>)}
        </>}
      </section>
    </div>
    {data.selection && <section className="rounded-xl border bg-white p-5"><h3 className="font-semibold">结构依赖上下文</h3>
      <p>{data.selection.scope}</p><p>关联对象 {data.selection.nodes.length} · 关联关系 {data.selection.edges.length}</p>
      <div className="max-h-64 overflow-auto flex flex-wrap gap-2 mt-3">{data.selection.nodes.map(n => <button className="border rounded p-2 text-sm" key={n.id} onClick={() => setSelected(n.id)}>{data.type_labels[n.entity_type]}：{String(n.properties.name)}</button>)}</div>
    </section>}
  </div>;
}
