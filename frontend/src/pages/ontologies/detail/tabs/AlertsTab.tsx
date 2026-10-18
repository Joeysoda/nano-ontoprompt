import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClientV2 } from '@/api/client';
import { explorerError } from '../object-explorer/runtime';
import type { RuleAlert } from '../object-explorer/ObjectAlerts';

type Alert = RuleAlert & { subject: { object_type: string; object_id: string }; created_at: string | null };
type Rule = { id: string; display_name: string };

const SEVERITY: Record<RuleAlert['severity'], [string, string]> = {
  low: ['低', 'bg-slate-100 text-slate-700'], medium: ['中', 'bg-amber-100 text-amber-800'],
  high: ['高', 'bg-orange-100 text-orange-800'], critical: ['严重', 'bg-red-100 text-red-800'],
};
const STATUS_FILTERS = [['open', '待处理'], ['acknowledged', '已确认'], ['resolved', '已解决'], ['', '全部']] as const;
const STATUS_LABEL: Record<RuleAlert['status'], string> = { open: '待处理', acknowledged: '已确认', resolved: '已解决' };
const PAGE = 50;

const fmt = (value: string | null) => value ? new Date(value).toLocaleString('zh-CN', { hour12: false }) : '—';

/** Alert center: every rule alert of this ontology, filterable, with the acknowledge/resolve lifecycle. */
export default function AlertsTab({ ontologyId, onOpenRules }: { ontologyId: string; onOpenRules: () => void }) {
  const client = useQueryClient();
  const [status, setStatus] = useState<string>('open');
  const [ruleId, setRuleId] = useState('');
  const [offset, setOffset] = useState(0);
  const [error, setError] = useState('');
  const rules = useQuery({ queryKey: ['rules', ontologyId], queryFn: () => apiClientV2.get<{ rules: Rule[] }>(`/ontologies/${ontologyId}/rules`) });
  const params = new URLSearchParams({ limit: String(PAGE), offset: String(offset), ...(status ? { status } : {}), ...(ruleId ? { rule_id: ruleId } : {}) });
  const alerts = useQuery({ queryKey: ['alerts', ontologyId, status, ruleId, offset],
    queryFn: () => apiClientV2.get<{ alerts: Alert[]; total: number }>(`/ontologies/${ontologyId}/alerts?${params}`) });
  const transition = useMutation({
    mutationFn: ({ id, verb, note }: { id: string; verb: 'acknowledge' | 'resolve'; note: string }) =>
      apiClientV2.post(`/ontologies/${ontologyId}/alerts/${id}:${verb}`, { note }),
    onSuccess: () => { setError(''); void client.invalidateQueries({ queryKey: ['alerts', ontologyId] }); },
    onError: err => setError(explorerError(err)),
  });
  const ruleName = new Map((rules.data?.rules ?? []).map(rule => [rule.id, rule.display_name]));
  const resolve = (alert: Alert) => {
    const note = window.prompt('解决说明（可选）', '');
    if (note !== null) transition.mutate({ id: alert.id, verb: 'resolve', note });
  };

  return <section aria-label="告警中心" className="space-y-4">
    <header className="flex flex-wrap items-center gap-3">
      <div>
        <h2 className="text-base font-semibold text-slate-900">告警中心</h2>
        <p className="text-xs text-slate-500">规则在固定数据视图上评估后产生的告警；同一段异常只记一条，持续时更新。</p>
      </div>
      <div className="ml-auto flex flex-wrap items-center gap-2">
        <div role="group" aria-label="按状态筛选" className="flex overflow-hidden rounded-md border border-slate-300">
          {STATUS_FILTERS.map(([value, label]) => <button key={label} type="button" aria-pressed={status === value}
            onClick={() => { setStatus(value); setOffset(0); }}
            className={`px-3 py-1.5 text-xs ${status === value ? 'bg-slate-800 text-white' : 'bg-white text-slate-700 hover:bg-slate-50'}`}>{label}</button>)}
        </div>
        <select aria-label="按规则筛选" value={ruleId} onChange={event => { setRuleId(event.target.value); setOffset(0); }} className="rounded-md border border-slate-300 px-2 py-1.5 text-xs">
          <option value="">全部规则</option>
          {(rules.data?.rules ?? []).map(rule => <option key={rule.id} value={rule.id}>{rule.display_name}</option>)}
        </select>
      </div>
    </header>
    {error && <p role="alert" className="text-xs text-red-700">{error}</p>}
    {alerts.isPending && <p role="status" className="text-sm text-slate-500">正在加载告警…</p>}
    {alerts.error && <p role="alert" className="text-sm text-red-700">{explorerError(alerts.error)}</p>}
    {alerts.data && alerts.data.total === 0 && <div className="rounded-lg border border-dashed border-slate-300 bg-white p-8 text-center text-sm text-slate-500">
      没有符合条件的告警。{(rules.data?.rules.length ?? 0) === 0 && <> 这个本体还没有规则，<button type="button" className="text-blue-700 underline" onClick={onOpenRules}>去创建运行规则</button>。</>}
    </div>}
    {alerts.data && alerts.data.total > 0 && <div className="overflow-auto rounded-lg border border-slate-200 bg-white">
      <table className="w-full min-w-[860px] text-left text-xs">
        <thead className="bg-slate-50 text-[11px] uppercase tracking-wide text-slate-500">
          <tr><th className="px-3 py-2">级别</th><th className="px-3 py-2">规则</th><th className="px-3 py-2">对象</th><th className="px-3 py-2">异常时间段</th>
            <th className="px-3 py-2">评估次数</th><th className="px-3 py-2">状态</th><th className="px-3 py-2">操作</th></tr>
        </thead>
        <tbody>{alerts.data.alerts.map(alert => <tr key={alert.id} className="border-t border-slate-100" data-testid="alert-row">
          <td className="px-3 py-2"><span className={`rounded px-1.5 py-0.5 font-medium ${SEVERITY[alert.severity][1]}`}>{SEVERITY[alert.severity][0]}</span></td>
          <td className="px-3 py-2 text-slate-700">{ruleName.get(alert.rule_id) ?? alert.rule_id.slice(0, 8)}</td>
          <td className="px-3 py-2"><span className="text-slate-500">{alert.subject.object_type} · </span><strong className="font-medium text-slate-800">{alert.subject.object_id}</strong></td>
          <td className="px-3 py-2 text-slate-600">{alert.window_start ? `${fmt(alert.window_start)} → ${fmt(alert.window_end)}` : '当前满足条件'}
            {alert.recovered_at && alert.status !== 'resolved' && <span className="ml-2 rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">已恢复</span>}</td>
          <td className="px-3 py-2 text-slate-600">{alert.occurrence_count}</td>
          <td className="px-3 py-2 text-slate-700">{STATUS_LABEL[alert.status]}</td>
          <td className="px-3 py-2"><div className="flex gap-2">
            {alert.status === 'open' && <button type="button" disabled={transition.isPending} onClick={() => transition.mutate({ id: alert.id, verb: 'acknowledge', note: '' })} className="rounded border border-slate-300 px-2 py-1 hover:border-slate-600">确认</button>}
            {alert.status !== 'resolved' && <button type="button" disabled={transition.isPending} onClick={() => resolve(alert)} className="rounded border border-slate-300 px-2 py-1 hover:border-slate-600">解决</button>}
            {alert.status === 'resolved' && <span className="text-slate-400">{alert.resolution_note || '—'}</span>}
          </div></td>
        </tr>)}</tbody>
      </table>
      <footer className="flex items-center justify-between border-t border-slate-100 px-3 py-2 text-xs text-slate-500">
        <span>共 {alerts.data.total} 条</span>
        <div className="flex gap-2">
          <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))} className="rounded border px-2 py-1 disabled:opacity-40">上一页</button>
          <button type="button" disabled={offset + PAGE >= alerts.data.total} onClick={() => setOffset(offset + PAGE)} className="rounded border px-2 py-1 disabled:opacity-40">下一页</button>
        </div>
      </footer>
    </div>}
  </section>;
}
