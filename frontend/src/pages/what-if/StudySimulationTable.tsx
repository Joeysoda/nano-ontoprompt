import { useState } from 'react'
import type { EditableStudySpec } from './StudySpecBuilder'
import { formatStudyValue as display } from './studyDisplay'

type SimulationCase = { id: string; name: string; case_kind: string; parameter_overrides: Record<string, unknown>; etag: number }
type Result = { case: SimulationCase; run_status: string; metrics: Array<{ key: string; value: unknown; unit: string }>; deltas_from_baseline: Record<string, { absolute: number; unit: string }> }
type Parameter = { key?: unknown; label?: unknown; unit?: unknown; type?: unknown; default?: unknown }

function InputValue({ value }: { value: unknown }) {
  if (value && typeof value === 'object') return <details className="max-w-44"><summary className="cursor-pointer">{Array.isArray(value) ? `${value.length} 条记录` : '结构化数据'}</summary><pre className="mt-2 max-h-36 overflow-auto whitespace-pre-wrap break-all text-[10px]">{JSON.stringify(value, null, 2)}</pre></details>
  return <span title={String(value)}>{display(value)}</span>
}

function inputValue(parameter: Parameter, value: unknown) {
  if (value && typeof value === 'object') return JSON.stringify(value)
  return value === null || value === undefined ? '' : String(value)
}

function parseInput(parameter: Parameter, value: string): unknown {
  if (parameter.type === 'integer') return value === '' ? null : Number.parseInt(value, 10)
  if (parameter.type === 'decimal') return value === '' ? null : Number.parseFloat(value)
  if (parameter.type === 'boolean') return value === 'true'
  if (parameter.type === 'json' || parameter.type === 'object_set') return JSON.parse(value)
  return value
}

export default function StudySimulationTable({ spec, cases, results, onEditCase }: { spec: EditableStudySpec; cases: SimulationCase[]; results: Result[]; onEditCase?: (target: SimulationCase, key: string, value: unknown) => void }) {
  const [drafts, setDrafts] = useState<Record<string, string>>({})
  return <div className="overflow-auto border border-slate-200 bg-white"><table className="w-full border-collapse text-left text-xs" aria-label="情景输入输出对比">
    <thead className="bg-slate-50"><tr><th className="min-w-32 border-b p-3">输入 / 输出</th><th className="min-w-20 border-b p-3">初始值</th>{cases.map(item => <th key={item.id} className="min-w-24 border-b border-l p-3"><span className="block text-[10px] font-normal text-slate-500">{item.case_kind === 'baseline' ? '基线' : '候选方案'}</span>{item.name}</th>)}</tr></thead>
    <tbody><tr><th className="bg-slate-50 px-3 py-2 text-slate-500" colSpan={cases.length + 2}>输入参数</th></tr>{spec.parameters.map(parameter => <tr key={String(parameter.key)}><th className="border-b p-3 font-normal">{String(parameter.label || parameter.key)}<span className="ml-1 text-slate-400">{String(parameter.unit || '')}</span></th><td className="border-b p-3 text-slate-500"><InputValue value={parameter.default} /></td>{cases.map(item => { const key = String(parameter.key); const draftKey = `${item.id}:${key}`; const overridden = Object.hasOwn(item.parameter_overrides, key); const value = overridden ? item.parameter_overrides[key] : parameter.default; const editable = item.case_kind !== 'baseline' && Boolean(onEditCase); return <td key={item.id} className={`border-b border-l p-3 ${overridden ? 'bg-blue-50 font-medium text-blue-700' : 'text-slate-500'}`}>{editable ? <input aria-label={`${item.name} ${String(parameter.label || key)}`} className="w-full min-w-20 rounded border border-slate-200 bg-white px-2 py-1 text-xs text-slate-800" type={parameter.type === 'integer' || parameter.type === 'decimal' ? 'number' : 'text'} value={drafts[draftKey] ?? inputValue(parameter, value)} onChange={event => setDrafts(current => ({ ...current, [draftKey]: event.target.value }))} onBlur={event => { try { onEditCase?.(item, key, parseInput(parameter, event.target.value)); setDrafts(current => { const next = { ...current }; delete next[draftKey]; return next }) } catch { /* Keep malformed JSON visible until corrected. */ } }} /> : <InputValue value={value} />}</td> })}</tr>)}
      <tr><th className="bg-slate-50 px-3 py-2 text-slate-500" colSpan={cases.length + 2}>输出指标</th></tr>{spec.metrics.map(metric => <tr key={String(metric.key)}><th className="border-b p-3 font-normal">{String(metric.label || metric.key)}</th><td className="border-b p-3 text-slate-400">—</td>{cases.map(item => { const result = results.find(result => result.case.id === item.id); const value = result?.metrics.find(value => value.key === metric.key); const delta = result?.deltas_from_baseline[String(metric.key)]; return <td key={item.id} className="border-b border-l p-3"><span title={value ? String(value.value) : undefined}>{value ? `${display(value.value)} ${value.unit}` : '—'}</span>{delta && <span className="mt-1 block text-[10px] text-slate-500">Δ {delta.absolute > 0 ? '+' : ''}{display(delta.absolute)} {delta.unit}</span>}</td> })}</tr>)}
      <tr><th className="p-3 font-normal text-slate-500">运行状态</th><td />{cases.map(item => <td key={item.id} className="border-l p-3 text-slate-500">{results.find(result => result.case.id === item.id)?.run_status || '未运行'}</td>)}</tr>
    </tbody>
  </table>{!cases.length && <p className="p-3 text-xs text-slate-500">创建基线和候选方案后，在这里并列查看输入与计算结果。</p>}</div>
}
