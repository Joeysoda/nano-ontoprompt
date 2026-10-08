import { useEffect, useState } from 'react';
import { apiClientV2, getApiErrorStatus } from '@/api/client';
import type { ObjectPanelRecord } from './ObjectPanel';

type ActionDefinition = {
  id: string;
  name: string;
  target_entity_type?: string | null;
  status: string;
  enabled: boolean;
  parameters: { name: string; type?: string; required?: boolean }[];
};
type Preview = { target: string; edits: { op: string; property?: string; value?: unknown }[] };
type ActionRun = { id: string; status: string; action_type_id: string; error?: string | null; side_effect_results?: { op: string }[] | null };

function message(error: unknown): string {
  const status = getApiErrorStatus(error);
  const fallback: Record<number, string> = {
    403: '没有执行此操作的权限',
    409: '对象已变化，请刷新后重新预览',
    410: '操作上下文已过期，请重新打开对象',
    503: '操作服务暂不可用，请稍后重试',
  };
  if (error && typeof error === 'object' && 'response' in error) {
    const response = (error as { response?: { data?: { detail?: unknown } } }).response;
    const detail = response?.data?.detail;
    if (typeof detail === 'string') return detail;
    if (detail && typeof detail === 'object') {
      const record = detail as { message?: string; error?: string };
      return record.message || record.error || '操作未通过校验';
    }
  }
  if (error && typeof error === 'object') {
    const payload = error as { detail?: unknown; message?: unknown; error?: unknown };
    if (typeof payload.detail === 'string') return payload.detail;
    if (payload.detail && typeof payload.detail === 'object') {
      const detail = payload.detail as { message?: unknown; error?: unknown };
      if (typeof detail.message === 'string') return detail.message;
      if (typeof detail.error === 'string') return detail.error;
    }
    if (typeof payload.message === 'string') return payload.message;
    if (typeof payload.error === 'string') return payload.error;
  }
  if (status && fallback[status]) return fallback[status];
  return error instanceof Error ? error.message : '操作未通过校验';
}

export default function ObjectActionRunner({ ontologyId, object, onClose, onCommitted }: {
  ontologyId: string;
  object: ObjectPanelRecord;
  onClose: () => void;
  onCommitted?: (message?: string) => void;
}) {
  const [actions, setActions] = useState<ActionDefinition[]>([]);
  const [selected, setSelected] = useState('');
  const [values, setValues] = useState<Record<string, unknown>>({});
  const [draft, setDraft] = useState<Record<string, string>>({});
  const [invalid, setInvalid] = useState<Record<string, string>>({});
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState('');
  const [success, setSuccess] = useState('');
  const [runs, setRuns] = useState<ActionRun[]>([]);
  const action = actions.find((item) => item.id === selected);

  useEffect(() => {
    let active = true;
    apiClientV2.get<ActionDefinition[]>(`/ontologies/${ontologyId}/actions`).then((result) => {
      if (!active) return;
      setActions(result.filter((item) => item.enabled && item.status === 'published' && (!item.target_entity_type || item.target_entity_type === object.object_type)));
    }).catch((cause: unknown) => { if (active) setError(message(cause)); });
    return () => { active = false; };
  }, [ontologyId, object.object_type]);

  async function refreshRuns() {
    const items = await apiClientV2.get<ActionRun[]>(`/ontologies/${ontologyId}/action-runs`, { params: { target_object_id: object.object_id, limit: 10 } });
    setRuns(items);
  }
  useEffect(() => {
    let active = true;
    apiClientV2.get<ActionRun[]>(`/ontologies/${ontologyId}/action-runs`, { params: { target_object_id: object.object_id, limit: 10 } })
      .then(items => { if (active) setRuns(items); }).catch(() => {});
    return () => { active = false; };
  }, [ontologyId, object.object_id]);

  const payload = { target_object_id: object.object_id, parameters: values,
    context: { ontology_id: ontologyId, consistency: 'live' } };
  function updateParameter(name: string, type: string | undefined, raw: string) {
    setDraft(previous => ({ ...previous, [name]: raw }));
    setPreview(null);
    const kind = (type || 'string').toLowerCase();
    let value: unknown = raw;
    let issue = '';
    if (raw === '') value = undefined;
    else if (kind === 'boolean') value = raw === 'true';
    else if (['number', 'integer', 'decimal'].includes(kind)) {
      value = Number(raw);
      if (!Number.isFinite(value) || (kind === 'integer' && !Number.isInteger(value))) issue = '请输入有效数字';
    } else if (['object', 'struct', 'array'].includes(kind)) {
      try {
        value = JSON.parse(raw);
        if ((kind === 'array' && !Array.isArray(value)) || (kind !== 'array' && (value === null || Array.isArray(value) || typeof value !== 'object'))) issue = 'JSON 类型与参数定义不一致';
      } catch { issue = '请输入有效 JSON'; }
    }
    setInvalid(previous => ({ ...previous, [name]: issue }));
    setValues(previous => ({ ...previous, [name]: value }));
  }
  async function showPreview() {
    if (!action) return;
    if (Object.values(invalid).some(Boolean)) { setError('请先修正参数格式'); return; }
    setBusy(true); setError(''); setSuccess(''); setPreview(null);
    try {
      const result = await apiClientV2.post<Preview>(`/ontologies/${ontologyId}/actions/${action.id}/preview`, payload);
      setPreview(result);
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  async function submit() {
    if (!action || !preview) return;
    setBusy(true); setError('');
    try {
      await apiClientV2.post(`/ontologies/${ontologyId}/actions/${action.id}/run`, payload);
      setPreview(null);
      onCommitted?.('操作已提交到正式数据。');
      onClose();
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }
  async function revert(runId: string) {
    setBusy(true); setError(''); setSuccess('');
    try {
      await apiClientV2.post(`/ontologies/${ontologyId}/action-runs/${runId}:revert`, {});
      setSuccess('属性编辑已回退。'); onCommitted?.('属性编辑已回退。'); await refreshRuns().catch(() => {});
    } catch (cause) { setError(message(cause)); }
    finally { setBusy(false); }
  }

  return <div className="fixed inset-0 z-50 grid place-items-center bg-slate-900/50 p-4" role="presentation" onMouseDown={(event) => { if (event.target === event.currentTarget) onClose(); }}>
    <section role="dialog" aria-modal="true" aria-label="执行对象操作" className="w-full max-w-lg rounded-xl bg-white p-5 shadow-xl">
      <div className="flex items-start justify-between gap-3"><div><h2 className="text-base font-semibold">执行操作</h2><p className="text-xs text-slate-500">{object.object_type} · {object.object_id} · 正式数据</p></div><button type="button" onClick={onClose} aria-label="关闭操作">×</button></div>
      <label className="mt-4 block text-sm">操作
        <select className="mt-1 w-full rounded border p-2" value={selected} onChange={(event) => { setSelected(event.target.value); setValues({}); setDraft({}); setInvalid({}); setPreview(null); setError(''); }}>
          <option value="">选择已发布操作</option>{actions.map((item) => <option key={item.id} value={item.id}>{item.name}</option>)}
        </select>
      </label>
      {action?.parameters?.map((parameter) => <label key={parameter.name} className="mt-3 block text-sm">{parameter.name}{parameter.required && ' *'}
        {parameter.type === 'boolean' ? <select className="mt-1 w-full rounded border p-2" aria-label={parameter.name} value={draft[parameter.name] || ''} onChange={event => updateParameter(parameter.name, parameter.type, event.target.value)}><option value="">选择</option><option value="true">true</option><option value="false">false</option></select>
          : ['object', 'struct', 'array'].includes(parameter.type || '') ? <textarea className="mt-1 w-full rounded border p-2 font-mono text-xs" aria-label={parameter.name} placeholder={parameter.type === 'array' ? '[]' : '{}'} value={draft[parameter.name] || ''} onChange={event => updateParameter(parameter.name, parameter.type, event.target.value)} />
          : <input className="mt-1 w-full rounded border p-2" aria-label={parameter.name} value={draft[parameter.name] || ''} onChange={event => updateParameter(parameter.name, parameter.type, event.target.value)} />}
        {invalid[parameter.name] && <span className="mt-1 block text-xs text-red-700">{invalid[parameter.name]}</span>}
      </label>)}
      {!actions.length && !error && <p className="mt-3 text-xs text-slate-500">此对象类型没有可用的已发布操作。</p>}
      {error && <p role="alert" className="mt-3 text-sm text-red-700">{error}</p>}
      {success && <p role="status" className="mt-3 text-sm text-green-700">{success}</p>}
      {preview && <div className="mt-4 rounded border bg-slate-50 p-3 text-xs"><p className="font-medium">将提交 {preview.edits.filter((edit) => edit.op !== 'invoke_action').length} 项编辑到 {preview.target}</p><ul className="mt-2 space-y-1">{preview.edits.filter((edit) => edit.op !== 'invoke_action').map((edit, index) => <li key={index}>{edit.op}{edit.property ? ` · ${edit.property}` : ''}{edit.value !== undefined ? ` → ${String(edit.value)}` : ''}</li>)}</ul></div>}
      {!!runs.length && <div className="mt-4 max-h-32 overflow-auto border-t pt-3"><h3 className="text-xs font-medium">最近的对象操作</h3>{runs.map(run => <div key={run.id} className="mt-2 flex items-center justify-between text-xs"><span>{run.action_type_id} · {run.status}</span>{run.status === 'completed' && !!run.side_effect_results?.length && run.side_effect_results.every(item => item.op === 'set_property' || item.op === 'unset_property') && <button type="button" disabled={busy} onClick={() => void revert(run.id)} className="text-blue-700">回退属性编辑</button>}</div>)}</div>}
      <div className="mt-5 flex justify-end gap-2"><button type="button" className="rounded border px-3 py-2 text-sm" onClick={onClose}>关闭</button><button type="button" className="rounded border px-3 py-2 text-sm disabled:opacity-50" disabled={!selected || busy} onClick={() => void showPreview()}>预览</button><button type="button" className="rounded bg-blue-700 px-3 py-2 text-sm text-white disabled:opacity-50" disabled={!preview || busy} onClick={() => void submit()}>确认提交</button></div>
    </section>
  </div>;
}
