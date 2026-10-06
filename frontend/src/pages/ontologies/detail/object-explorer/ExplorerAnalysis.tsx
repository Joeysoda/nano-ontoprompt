import { useState } from 'react';
import { useQuery } from '@tanstack/react-query';
import { ArrowRight, BarChart3, GitCompareArrows, X } from 'lucide-react';
import { apiClientV2 } from '@/api/client';
import type { ObjectSetExpression } from './contract';
import type { ExplorerContext } from './ExplorerOperations';
import { explorerError } from './runtime';

type Aggregate = { groups: Record<string, unknown>[]; exact: boolean; completeness: string };
type Compare = { added: Array<{ object_type: string; object_id: string }>; removed: Array<{ object_type: string; object_id: string }>; retained: Array<{ object_type: string; object_id: string; baseline: Record<string, unknown>; candidate: Record<string, unknown> }> };

export default function ExplorerAnalysis({ ontologyId, expression, context, properties, onFilter }: {
  ontologyId: string; expression: ObjectSetExpression; context: ExplorerContext; properties: string[]; onFilter: (property: string, value: unknown) => void;
}) {
  const [group, setGroup] = useState('');
  const [candidate, setCandidate] = useState('');
  const [compareView, setCompareView] = useState('');
  const [showCompare, setShowCompare] = useState(false);
  const root = `/ontologies/${ontologyId}/object-query`;
  const aggregate = useQuery({ queryKey: ['explorer-aggregate', ontologyId, expression, context, group], enabled: !!context.data_view_id,
    queryFn: ({ signal }) => apiClientV2.post<Aggregate>(`${root}/aggregate`, { expression, context, group_by: group ? [{ api_name: group }] : [], aggregations: [{ op: 'count', alias: 'count' }] }, { signal }),
  });
  const compare = useQuery({ queryKey: ['explorer-compare', ontologyId, expression, context, compareView], enabled: !!context.data_view_id && !!compareView,
    queryFn: ({ signal }) => apiClientV2.post<Compare>(`${root}/compare`, { expression, baseline_context: context, candidate_context: { ontology_id: ontologyId, consistency: 'snapshot', data_view_id: compareView }, mode: 'reevaluate' }, { signal }),
  });
  const total = compare.data ? compare.data.added.length + compare.data.removed.length + compare.data.retained.length : 0;

  return <section className={`oe-analysis ${showCompare ? 'is-open' : ''}`} aria-label="完整统计与 Compare">
    <div className="oe-analysis-bar">
      <label><BarChart3 size={14} /><span>完整统计</span><select aria-label="聚合属性" value={group} disabled={!context.data_view_id} onChange={event => setGroup(event.target.value)}><option value="">总对象数</option>{properties.map(property => <option key={property}>{property}</option>)}</select></label>
      <button type="button" className={showCompare ? 'active' : ''} onClick={() => setShowCompare(value => !value)}><GitCompareArrows size={15} />Compare</button>
      {!context.data_view_id && <span className="oe-analysis-hint">固定当前数据后可运行精确统计和比较</span>}
      {aggregate.data && <span className="oe-analysis-meta">{aggregate.data.exact ? '精确' : '估计'} · {aggregate.data.completeness}</span>}
      {aggregate.data && <div className="oe-aggregate-chips">{aggregate.data.groups.slice(0, 6).map((row, index) => <button type="button" key={index} disabled={!group || aggregate.isFetching} onClick={() => onFilter(group, row[group])}>{group ? String(row[group] ?? '无值') : '对象总数'} <strong>{String(row.count)}</strong></button>)}</div>}
    </div>

    {showCompare && <div className="oe-compare-panel">
      <div className="oe-compare-picker">
        <div className="oe-set-pill oe-set-pill--baseline"><span /> <div><small>BASELINE</small><strong>{context.data_view_id || '需要固定视图'}</strong></div></div>
        <ArrowRight size={16} />
        <label className="oe-set-pill oe-set-pill--candidate"><span /><div><small>CANDIDATE</small><input aria-label="比较视图" value={candidate} onChange={event => setCandidate(event.target.value)} placeholder="固定视图 ID" /></div></label>
        <button type="button" className="primary" disabled={!context.data_view_id || !candidate.trim()} onClick={() => setCompareView(candidate.trim())}>运行比较</button>
        <button type="button" className="oe-compare-close" aria-label="关闭 Compare" onClick={() => setShowCompare(false)}><X size={16} /></button>
      </div>
      {(aggregate.isFetching || compare.isFetching) && <p role="status" className="oe-operation-status">正在计算完整结果…</p>}
      {aggregate.error && <p role="alert" className="oe-operation-status oe-operation-status--error">{explorerError(aggregate.error)}</p>}
      {compare.error && <p role="alert" className="oe-operation-status oe-operation-status--error">{explorerError(compare.error)}</p>}
      {compare.data && <div className="oe-compare-results" aria-label="集合比较结果">
        <div className="oe-compare-summary">
          <div><span className="blue" style={{ width: `${total ? Math.max(8, ((compare.data.retained.length + compare.data.removed.length) / total) * 100) : 0}%` }} /><small>Baseline</small><strong>{compare.data.retained.length + compare.data.removed.length}</strong></div>
          <div><span className="orange" style={{ width: `${total ? Math.max(8, ((compare.data.retained.length + compare.data.added.length) / total) * 100) : 0}%` }} /><small>Candidate</small><strong>{compare.data.retained.length + compare.data.added.length}</strong></div>
          <dl><div><dt>新增</dt><dd>{compare.data.added.length}</dd></div><div><dt>移除</dt><dd>{compare.data.removed.length}</dd></div><div><dt>保留</dt><dd>{compare.data.retained.length}</dd></div></dl>
        </div>
        <div className="oe-table-wrap"><table className="oe-results-table oe-compare-table"><thead><tr><th>对象</th><th>变化</th><th><span className="oe-color-key blue" />Baseline</th><th><span className="oe-color-key orange" />Candidate</th></tr></thead><tbody>
          {compare.data.added.map(row => <tr key={`add:${row.object_type}:${row.object_id}`}><td>{row.object_type} · {row.object_id}</td><td><span className="oe-delta oe-delta--add">新增</span></td><td>—</td><td>存在</td></tr>)}
          {compare.data.removed.map(row => <tr key={`remove:${row.object_type}:${row.object_id}`}><td>{row.object_type} · {row.object_id}</td><td><span className="oe-delta oe-delta--remove">移除</span></td><td>存在</td><td>—</td></tr>)}
          {compare.data.retained.map(row => <tr key={`keep:${row.object_type}:${row.object_id}`}><td>{row.object_type} · {row.object_id}</td><td><span className="oe-delta">保留</span></td><td>{JSON.stringify(row.baseline)}</td><td>{JSON.stringify(row.candidate)}</td></tr>)}
        </tbody></table></div>
      </div>}
    </div>}
  </section>;
}
