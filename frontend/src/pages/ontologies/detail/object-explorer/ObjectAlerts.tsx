import { useCallback, useEffect, useState } from 'react';
import { apiClientV2 } from '@/api/client';
import { explorerError } from './runtime';
import type { ObjectPanelRecord } from './ObjectPanel';

export type RuleAlert = {
  id: string;
  rule_id: string;
  status: 'open' | 'acknowledged' | 'resolved';
  severity: 'low' | 'medium' | 'high' | 'critical';
  window_start: string | null;
  window_end: string | null;
  evidence: { peak?: number; breaching_windows?: number; ongoing?: boolean; evidence?: Record<string, unknown> };
  properties: Record<string, unknown>;
  suggested_action: { action_id: string; request: { target_object_id: string; parameters: Record<string, unknown> } } | null;
  occurrence_count: number;
  recovered_at: string | null;
  resolution_note: string;
};

const severityLabel: Record<RuleAlert['severity'], string> = { low: '低', medium: '中', high: '高', critical: '严重' };
const statusLabel: Record<RuleAlert['status'], string> = { open: '待处理', acknowledged: '已确认', resolved: '已解决' };
const severityClass: Record<RuleAlert['severity'], string> = {
  low: 'bg-slate-100 text-slate-700', medium: 'bg-amber-100 text-amber-800',
  high: 'bg-orange-100 text-orange-800', critical: 'bg-red-100 text-red-800',
};

function formatWindow(alert: RuleAlert): string {
  if (!alert.window_start) return '对象当前满足规则条件';
  const fmt = (value: string) => new Date(value).toLocaleString('zh-CN', { hour12: false });
  return `${fmt(alert.window_start)} → ${alert.window_end ? fmt(alert.window_end) : '进行中'}`;
}

export default function ObjectAlerts({ ontologyId, object }: { ontologyId: string; object: ObjectPanelRecord }) {
  const [alerts, setAlerts] = useState<RuleAlert[] | null>(null);
  const [error, setError] = useState('');
  const [busy, setBusy] = useState<string | null>(null);
  const [preview, setPreview] = useState<Record<string, string>>({});
  const [showResolved, setShowResolved] = useState(false);

  const load = useCallback(async (signal?: AbortSignal) => {
    const query = new URLSearchParams({ subject_type: object.object_type, subject_id: object.object_id, limit: '50' });
    try {
      const body = await apiClientV2.get<{ alerts: RuleAlert[] }>(`/ontologies/${ontologyId}/alerts?${query}`, { signal });
      setAlerts(body.alerts); setError('');
    } catch (err) {
      if (!signal?.aborted) { setAlerts(null); setError(explorerError(err)); }
    }
  }, [ontologyId, object.object_type, object.object_id]);

  useEffect(() => {
    const controller = new AbortController();
    void load(controller.signal);
    return () => controller.abort();
  }, [load]);

  const transition = async (alert: RuleAlert, verb: 'acknowledge' | 'resolve') => {
    if (busy) return;
    let note = '';
    if (verb === 'resolve') {
      const input = window.prompt('解决说明（可选）', '');
      if (input === null) return;
      note = input;
    }
    setBusy(alert.id);
    try {
      await apiClientV2.post(`/ontologies/${ontologyId}/alerts/${alert.id}:${verb}`, { note });
      await load();
    } catch (err) {
      setError(explorerError(err));
      await load();
    } finally {
      setBusy(null);
    }
  };

  const previewAction = async (alert: RuleAlert) => {
    if (!alert.suggested_action || busy) return;
    setBusy(alert.id);
    try {
      const body = await apiClientV2.post<{ edit_count: number; target: string }>(
        `/ontologies/${ontologyId}/actions/${alert.suggested_action.action_id}/preview`,
        { ...alert.suggested_action.request, context: { ontology_id: ontologyId, consistency: 'live' } });
      setPreview(current => ({ ...current, [alert.id]: `预览：将对 ${body.target} 产生 ${body.edit_count} 项编辑；请通过“操作”确认提交` }));
    } catch (err) {
      setPreview(current => ({ ...current, [alert.id]: `预览失败：${explorerError(err)}` }));
    } finally {
      setBusy(null);
    }
  };

  const visible = (alerts || []).filter(alert => showResolved || alert.status !== 'resolved');
  const resolvedCount = (alerts || []).length - (alerts || []).filter(alert => alert.status !== 'resolved').length;

  return <section className="border-t px-3 py-3" aria-label="规则告警">
    <header className="mb-2 flex items-center justify-between">
      <p className="text-xs font-medium text-slate-500">规则告警{alerts ? ` · ${alerts.length - resolvedCount} 条未解决` : ''}</p>
      {resolvedCount > 0 && <button type="button" className="text-xs text-blue-700" aria-pressed={showResolved} onClick={() => setShowResolved(value => !value)}>
        {showResolved ? '隐藏已解决' : `显示已解决（${resolvedCount}）`}
      </button>}
    </header>
    {alerts === null && !error && <p role="status" className="text-xs text-slate-500">正在加载告警…</p>}
    {error && <p role="alert" className="text-xs text-red-700">{error} <button type="button" className="ml-1 underline" onClick={() => void load()}>重试</button></p>}
    {alerts && visible.length === 0 && <p className="text-xs text-slate-500">暂无未解决的告警。</p>}
    <ul className="space-y-2">
      {visible.map(alert => <li key={alert.id} className="rounded border px-2 py-2 text-xs" data-testid="rule-alert">
        <div className="flex flex-wrap items-center gap-1">
          <span className={`rounded px-1.5 py-0.5 font-medium ${severityClass[alert.severity]}`}>{severityLabel[alert.severity]}</span>
          <span className="rounded bg-slate-50 px-1.5 py-0.5">{statusLabel[alert.status]}</span>
          {alert.recovered_at && alert.status !== 'resolved' && <span className="rounded bg-emerald-50 px-1.5 py-0.5 text-emerald-700">条件已恢复</span>}
          {alert.occurrence_count > 1 && <span className="text-slate-500">评估 {alert.occurrence_count} 次</span>}
        </div>
        <p className="mt-1 text-slate-700">{formatWindow(alert)}</p>
        {alert.evidence.peak !== undefined && <p className="text-slate-500">峰值 {alert.evidence.peak}，越线窗口 {alert.evidence.breaching_windows}</p>}
        {alert.status === 'resolved' && alert.resolution_note && <p className="text-slate-500">说明：{alert.resolution_note}</p>}
        <div className="mt-2 flex flex-wrap gap-2">
          {alert.status === 'open' && <button type="button" disabled={busy === alert.id} onClick={() => void transition(alert, 'acknowledge')}>确认</button>}
          {alert.status !== 'resolved' && <button type="button" disabled={busy === alert.id} onClick={() => void transition(alert, 'resolve')}>解决</button>}
          {alert.suggested_action && alert.status !== 'resolved' && <button type="button" disabled={busy === alert.id} onClick={() => void previewAction(alert)}>预览建议操作</button>}
        </div>
        {preview[alert.id] && <p role="status" className="mt-1 text-slate-600">{preview[alert.id]}</p>}
      </li>)}
    </ul>
  </section>;
}
