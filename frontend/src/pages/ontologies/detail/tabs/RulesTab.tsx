import { useState } from 'react';
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query';
import { apiClientV2 } from '@/api/client';
import { useAuthStore } from '@/stores/authStore';
import { explorerError } from '../object-explorer/runtime';

type Schedule = { interval_minutes: number | null; source: string | null; last_scheduled_at: string | null; error: { code: string; message: string } | null };
type Rule = { id: string; api_name: string; display_name: string; description: string; status: 'active' | 'muted' | 'paused' | 'retired';
              current_version: number; owner_id: string; schedule: Schedule };
type Proposal = { id: string; base_version: number; status: string; author_id: string; reviewer_id: string | null; self_approved: boolean;
                  description: string; old_logic: unknown; new_logic: unknown };
type Detail = { rule: Rule; versions: Array<{ version: number; logic: unknown }>; proposals: Proposal[] };
type Evaluation = { id: string; status: string; data_view_id: string; output_count: number; triggered_count: number; recovered_count: number; error: { code: string } | null };

const STATUS: Record<Rule['status'], [string, string]> = {
  active: ['运行中', 'bg-emerald-50 text-emerald-700'], muted: ['静音', 'bg-amber-50 text-amber-700'],
  paused: ['已暂停', 'bg-slate-100 text-slate-600'], retired: ['已退役', 'bg-slate-100 text-slate-400'],
};
const STATUS_HELP: Record<string, string> = {
  active: '照常评估并给出建议操作', muted: '照常评估和记录告警，但不给建议操作', paused: '停止定时评估，仍可手动评估',
};
const TEMPLATE = JSON.stringify({
  input: { kind: 'base', type_ref: { api_name: '对象类型' } },
  blocks: [{ kind: 'filter', where: { kind: 'comparison', property: { api_name: 'status' }, op: 'eq', value: 'down' } }],
  output: { severity: 'medium', properties: {}, evidence_properties: ['status'] },
}, null, 2);

/** Run-time rules: proposals and review, versions, status, schedule and evaluation history. */
export default function RulesTab({ ontologyId, onOpenAlerts }: { ontologyId: string; onOpenAlerts: () => void }) {
  const client = useQueryClient();
  const role = useAuthStore(state => state.user?.role);
  const canEdit = role === 'admin' || role === 'editor';
  const [selectedId, setSelectedId] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [draft, setDraft] = useState({ api_name: '', display_name: '', logic: TEMPLATE });
  const [message, setMessage] = useState('');
  const rules = useQuery({ queryKey: ['rules', ontologyId], queryFn: () => apiClientV2.get<{ rules: Rule[] }>(`/ontologies/${ontologyId}/rules`) });
  const activeId = selectedId ?? rules.data?.rules[0]?.id ?? null;
  const detail = useQuery({ queryKey: ['rule', ontologyId, activeId], enabled: !!activeId,
    queryFn: () => apiClientV2.get<Detail>(`/ontologies/${ontologyId}/rules/${activeId}`) });
  const evaluations = useQuery({ queryKey: ['rule-evaluations', ontologyId, activeId], enabled: !!activeId,
    queryFn: () => apiClientV2.get<{ evaluations: Evaluation[] }>(`/ontologies/${ontologyId}/rules/${activeId}/evaluations?limit=10`) });
  const refresh = () => {
    for (const key of ['rules', 'rule', 'rule-evaluations', 'alerts']) void client.invalidateQueries({ queryKey: [key, ontologyId] });
  };
  const act = useMutation({
    mutationFn: ({ path, body }: { path: string; body: unknown }) => apiClientV2.post<Record<string, unknown>>(`/ontologies/${ontologyId}/rules${path}`, body),
    onSuccess: (data, variables) => {
      if (variables.path.endsWith('evaluate:latest')) {
        setMessage(data.replayed ? '数据视图没有变化，沿用上次评估结果' : `评估完成：命中 ${data.output_count} 项，新增告警 ${data.triggered_count} 条，恢复 ${data.recovered_count} 条`);
      } else setMessage('已保存');
      refresh();
    },
    onError: error => setMessage(explorerError(error)),
  });
  const create = useMutation({
    mutationFn: async () => apiClientV2.post<{ rule: Rule }>(`/ontologies/${ontologyId}/rules`, {
      api_name: draft.api_name.trim(), display_name: draft.display_name.trim(), logic: JSON.parse(draft.logic) }),
    onSuccess: body => { setCreating(false); setSelectedId(body.rule.id); setMessage('规则已创建，提案等待审批'); refresh(); },
    onError: error => setMessage(error instanceof SyntaxError ? '规则逻辑不是合法的 JSON' : explorerError(error)),
  });
  const rule = detail.data?.rule;
  const open = detail.data?.proposals.filter(p => p.status === 'open') ?? [];

  return <section aria-label="运行规则" className="grid gap-5 lg:grid-cols-[280px_minmax(0,1fr)]">
    <aside className="space-y-2">
      <div className="flex items-center justify-between">
        <h2 className="text-base font-semibold text-slate-900">运行规则</h2>
        {canEdit && <button type="button" onClick={() => { setCreating(true); setMessage(''); }} className="rounded-md bg-slate-800 px-3 py-1.5 text-xs text-white">新建规则</button>}
      </div>
      <p className="text-xs text-slate-500">规则在固定数据视图上检查对象和时序数据，命中后在<button type="button" className="text-blue-700 underline" onClick={onOpenAlerts}>告警中心</button>产生告警。修改规则需要提案审批。</p>
      {rules.isPending && <p role="status" className="text-xs text-slate-500">正在加载规则…</p>}
      {rules.data?.rules.length === 0 && !creating && <p className="rounded-lg border border-dashed border-slate-300 p-4 text-xs text-slate-500">还没有规则。</p>}
      <ul className="space-y-1">{rules.data?.rules.map(item => <li key={item.id}>
        <button type="button" onClick={() => { setSelectedId(item.id); setCreating(false); setMessage(''); }} aria-current={item.id === activeId && !creating ? 'true' : undefined}
          className={`w-full rounded-lg border px-3 py-2 text-left ${item.id === activeId && !creating ? 'border-slate-800 bg-white shadow-sm' : 'border-slate-200 bg-white hover:border-slate-400'}`}>
          <span className="flex items-center justify-between gap-2"><strong className="truncate text-sm font-medium text-slate-900">{item.display_name}</strong>
            <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] ${STATUS[item.status][1]}`}>{STATUS[item.status][0]}</span></span>
          <span className="mt-0.5 block text-[11px] text-slate-500">{item.current_version ? `v${item.current_version}` : '待审批'}{item.schedule.interval_minutes ? ` · 每 ${item.schedule.interval_minutes} 分钟` : ' · 手动'}</span>
        </button></li>)}</ul>
    </aside>

    <div className="min-w-0 space-y-4">
      {message && <p role="status" className="rounded-md bg-slate-50 px-3 py-2 text-xs text-slate-700">{message}</p>}
      {creating && <form className="space-y-3 rounded-lg border border-slate-200 bg-white p-4" onSubmit={event => { event.preventDefault(); create.mutate(); }} aria-label="新建规则">
        <h3 className="text-sm font-semibold">新建规则</h3>
        <div className="grid gap-3 sm:grid-cols-2">
          <label className="text-xs text-slate-600">显示名称<input required value={draft.display_name} onChange={e => setDraft({ ...draft, display_name: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 text-sm" /></label>
          <label className="text-xs text-slate-600">API 名称（字母开头）<input required pattern="[A-Za-z][A-Za-z0-9_]*" value={draft.api_name} onChange={e => setDraft({ ...draft, api_name: e.target.value })} className="mt-1 w-full rounded border border-slate-300 px-2 py-1.5 font-mono text-sm" /></label>
        </div>
        <label className="block text-xs text-slate-600">规则逻辑（输入对象集合 → 过滤 / 时序窗口块 → 输出）
          <textarea value={draft.logic} onChange={e => setDraft({ ...draft, logic: e.target.value })} rows={12} spellCheck={false} className="mt-1 w-full rounded border border-slate-300 p-2 font-mono text-xs" /></label>
        <div className="flex gap-2"><button type="submit" disabled={create.isPending} className="rounded-md bg-slate-800 px-3 py-1.5 text-xs text-white">提交提案</button>
          <button type="button" onClick={() => setCreating(false)} className="rounded-md border border-slate-300 px-3 py-1.5 text-xs">取消</button></div>
      </form>}

      {!creating && rule && <>
        <header className="flex flex-wrap items-start gap-3 rounded-lg border border-slate-200 bg-white p-4">
          <div className="min-w-0">
            <h3 className="text-base font-semibold text-slate-900">{rule.display_name}</h3>
            <p className="font-mono text-[11px] text-slate-500">{rule.api_name} · {rule.current_version ? `当前版本 v${rule.current_version}` : '尚无已批准版本'}</p>
          </div>
          {canEdit && rule.current_version > 0 && <div className="ml-auto flex flex-wrap items-center gap-2">
            <label className="text-xs text-slate-600">状态 <select aria-label="规则状态" value={rule.status} disabled={act.isPending}
              onChange={e => act.mutate({ path: `/${rule.id}/status`, body: { status: e.target.value } })} className="rounded border border-slate-300 px-2 py-1 text-xs">
              {(['active', 'muted', 'paused'] as const).map(value => <option key={value} value={value}>{STATUS[value][0]}</option>)}</select></label>
            <button type="button" disabled={act.isPending} onClick={() => act.mutate({ path: `/${rule.id}/evaluate:latest`, body: {} })} className="rounded-md bg-slate-800 px-3 py-1.5 text-xs text-white">立即评估</button>
          </div>}
          {STATUS_HELP[rule.status] && <p className="w-full text-[11px] text-slate-500">{STATUS_HELP[rule.status]}</p>}
        </header>

        {open.length > 0 && <section className="rounded-lg border border-amber-200 bg-amber-50/40 p-4" aria-label="待审批提案">
          <h4 className="mb-2 text-sm font-semibold text-amber-900">待审批提案</h4>
          {open.map(proposal => <div key={proposal.id} className="space-y-2 border-t border-amber-100 pt-2 first:border-0 first:pt-0">
            <p className="text-xs text-slate-600">基于 v{proposal.base_version} 的修改{proposal.description ? `：${proposal.description}` : ''}</p>
            <div className="grid gap-2 md:grid-cols-2">
              <pre className="max-h-56 overflow-auto rounded bg-white p-2 text-[10px]" aria-label="修改前">{proposal.old_logic ? JSON.stringify(proposal.old_logic, null, 2) : '（新规则）'}</pre>
              <pre className="max-h-56 overflow-auto rounded bg-white p-2 text-[10px]" aria-label="修改后">{JSON.stringify(proposal.new_logic, null, 2)}</pre>
            </div>
            {canEdit && <div className="flex gap-2">
              <button type="button" disabled={act.isPending} onClick={() => act.mutate({ path: `/${rule.id}/proposals/${proposal.id}:review`, body: { decision: 'approve' } })} className="rounded-md bg-emerald-700 px-3 py-1.5 text-xs text-white">批准并发布</button>
              <button type="button" disabled={act.isPending} onClick={() => act.mutate({ path: `/${rule.id}/proposals/${proposal.id}:review`, body: { decision: 'reject' } })} className="rounded-md border border-slate-300 bg-white px-3 py-1.5 text-xs">驳回</button>
            </div>}
          </div>)}
        </section>}

        {canEdit && rule.current_version > 0 && <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="定时评估">
          <h4 className="mb-2 text-sm font-semibold">定时评估</h4>
          <div className="flex flex-wrap items-center gap-2 text-xs">
            <select aria-label="评估间隔" value={rule.schedule.interval_minutes ?? ''} disabled={act.isPending}
              onChange={e => act.mutate({ path: `/${rule.id}/schedule`, body: { interval_minutes: e.target.value ? Number(e.target.value) : null, source: rule.schedule.source ?? 'latest_view' } })}
              className="rounded border border-slate-300 px-2 py-1">
              <option value="">不定时（仅手动）</option>{[15, 60, 360, 1440].map(m => <option key={m} value={m}>每 {m >= 60 ? `${m / 60} 小时` : `${m} 分钟`}</option>)}
            </select>
            {rule.schedule.interval_minutes && <select aria-label="数据来源" value={rule.schedule.source ?? 'latest_view'} disabled={act.isPending}
              onChange={e => act.mutate({ path: `/${rule.id}/schedule`, body: { interval_minutes: rule.schedule.interval_minutes, source: e.target.value } })}
              className="rounded border border-slate-300 px-2 py-1">
              <option value="latest_view">最新固定视图（支持时序规则）</option><option value="live_snapshot">实时快照（仅对象规则）</option></select>}
            {rule.schedule.last_scheduled_at && <span className="text-slate-500">上次：{new Date(rule.schedule.last_scheduled_at).toLocaleString('zh-CN', { hour12: false })}</span>}
            {rule.schedule.error && <span className="text-red-700">失败：{rule.schedule.error.message}</span>}
          </div>
        </section>}

        <section className="rounded-lg border border-slate-200 bg-white p-4" aria-label="评估记录">
          <h4 className="mb-2 text-sm font-semibold">最近评估</h4>
          {!evaluations.data?.evaluations.length ? <p className="text-xs text-slate-500">还没有评估记录。</p> :
            <table className="w-full text-left text-xs"><thead className="text-[11px] text-slate-500"><tr><th className="py-1">结果</th><th>命中</th><th>新增告警</th><th>恢复</th><th>数据视图</th></tr></thead>
              <tbody>{evaluations.data.evaluations.map(item => <tr key={item.id} className="border-t border-slate-100">
                <td className="py-1.5">{item.status === 'succeeded' ? '成功' : <span className="text-red-700">失败 · {item.error?.code}</span>}</td>
                <td>{item.output_count}</td><td>{item.triggered_count}</td><td>{item.recovered_count}</td><td className="font-mono text-[10px] text-slate-500">{item.data_view_id.slice(0, 18)}</td>
              </tr>)}</tbody></table>}
        </section>
      </>}
    </div>
  </section>;
}
