import { useState } from 'react'
import { useMutation, useQuery } from '@tanstack/react-query'
import { apiClientV2, formatApiError } from '@/api/client'
import { pickWidgets, type AutobindResult } from './editorModel'
import { specOf, type DataSource, type Page } from './spec'

const ROLE: Record<string, string> = { time: '时间', measure: '度量', flag: '0/1 标记', category: '类别', key: '维度键', identifier: '标识符', other: '其他' }
const MODES = [['rules', '按规则（稳定、秒出）'], ['llm', 'AI 大模型（标题更好，结果可能每次不同）']] as const

/** One-click binding: pick an object type, preview the generated page, keep what you want. */
export default function AutobindDialog({ ontologyId, prefix, applyLabel, onApply, onClose }: {
  ontologyId: string; prefix: string; applyLabel: string; onApply: (picked: { data_sources: DataSource[]; page: Page }, result: AutobindResult) => void; onClose: () => void
}) {
  const catalog = useQuery({ queryKey: ['query-catalog', ontologyId], queryFn: () => apiClientV2.get<{ types: Array<{ api_name: string }> }>(`/ontologies/${ontologyId}/object-query/catalog`) })
  const [objectType, setObjectType] = useState('')
  const [mode, setMode] = useState<'rules' | 'llm'>('rules')
  const [keep, setKeep] = useState<Set<string>>(new Set())
  const generate = useMutation({
    mutationFn: () => apiClientV2.post<AutobindResult>(`/ontologies/${ontologyId}/workshop-apps:autobind`, { object_type: objectType, mode, prefix }, { timeout: 180000 }),
    onSuccess: result => setKeep(new Set(result.page.widgets.map(row => row.id))),
  })
  const result = generate.data
  return <div className="fixed inset-0 z-50 flex items-center justify-center bg-slate-900/40" role="dialog" aria-label="一键绑定" data-testid="autobind-dialog">
    <div className="max-h-[90vh] w-[880px] max-w-[95vw] overflow-auto rounded bg-white p-5 shadow-xl">
      <h2 className="text-base font-semibold">一键绑定：从对象类型生成页面</h2>
      <p className="mt-1 text-xs text-slate-500">平台读出这类对象有哪些字段、各是什么角色，自动配好数据源和组件。先预览，勾选要保留的，再生成。</p>
      <div className="mt-3 flex flex-wrap items-end gap-3">
        <label className="text-xs text-slate-500">对象类型<select className="wb-input mt-0.5 block w-56" aria-label="对象类型" value={objectType} onChange={event => setObjectType(event.target.value)}>
          <option value="">选择…</option>{catalog.data?.types.map(row => <option key={row.api_name} value={row.api_name}>{row.api_name}</option>)}</select></label>
        <fieldset className="flex gap-3 text-xs" aria-label="生成方式">{MODES.map(([value, label]) => <label key={value} className="flex items-center gap-1">
          <input type="radio" name="autobind-mode" checked={mode === value} onChange={() => setMode(value)} />{label}</label>)}</fieldset>
        <button className="wb-button-primary" disabled={!objectType || generate.isPending} onClick={() => generate.mutate()}>{generate.isPending ? '生成中…' : '生成预览'}</button>
      </div>
      {generate.error && <p role="alert" className="mt-3 text-sm text-rose-700">{formatApiError(generate.error)}</p>}
      {result && <div className="mt-4 grid gap-4 md:grid-cols-2">
        <section><h3 className="text-xs font-semibold text-slate-600">字段识别（{result.profile.label} · {result.profile.count.toLocaleString()} 个对象）</h3>
          <table className="mt-1 w-full text-xs" data-testid="autobind-fields"><tbody>{result.profile.fields.map(field => <tr key={field.name} className="border-b">
            <td className="py-1 pr-2">{field.label}{field.label !== field.name && <span className="text-slate-400"> {field.name}</span>}</td>
            <td className="pr-2">{ROLE[field.role] ?? field.role}{field.dimension ? ` → ${field.dimension}` : ''}</td><td className="text-slate-400">{field.distinct} 个值</td></tr>)}</tbody></table></section>
        <section><h3 className="text-xs font-semibold text-slate-600">将生成：{result.plan.page_title}{result.model && <span className="font-normal text-slate-400"> · {result.model}</span>}</h3>
          <ul className="mt-1 space-y-1 text-sm" data-testid="autobind-widgets">{result.page.widgets.map(widget => <li key={widget.id}><label className="flex items-center gap-2">
            <input type="checkbox" checked={keep.has(widget.id)} onChange={event => setKeep(current => { const next = new Set(current); if (event.target.checked) next.add(widget.id); else next.delete(widget.id); return next })} />
            <span className="rounded bg-slate-100 px-1.5 text-[11px] text-slate-600">{specOf(widget.type)?.label}</span>
            <span>{widget.title || (widget.type === 'kpi_strip' ? (widget.props.items as Array<{ label: string }>).map(row => row.label).join(' · ') : widget.id)}</span></label></li>)}</ul>
          {result.plan.reasoning && <p className="mt-2 rounded bg-violet-50 p-2 text-xs text-violet-900">AI 说明：{result.plan.reasoning}</p>}
          {result.dropped.length > 0 && <div className="mt-2 rounded bg-amber-50 p-2 text-xs text-amber-900" data-testid="autobind-dropped"><p>AI 的这些选择不符合数据，已丢弃：</p><ul className="list-disc pl-4">{result.dropped.map(row => <li key={row}>{row}</li>)}</ul></div>}
        </section>
      </div>}
      <div className="mt-4 flex justify-end gap-2">
        <button className="wb-button-secondary" onClick={onClose}>取消</button>
        <button className="wb-button-primary" disabled={!result || keep.size === 0} onClick={() => result && onApply(pickWidgets(result, keep), result)}>{applyLabel}</button>
      </div>
    </div>
  </div>
}
