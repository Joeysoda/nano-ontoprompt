import { useState } from 'react'

const renderValue = (value: unknown) => value == null ? '' : typeof value === 'object' ? JSON.stringify(value) : String(value)
export default function StudyCaseInputs({ parameters, value, onChange }: { parameters: Array<Record<string, unknown>>; value: string; onChange: (value: string) => void }) {
  const [error, setError] = useState('')
  let overrides: Record<string, unknown>
  try { overrides = JSON.parse(value); if (!overrides || Array.isArray(overrides) || typeof overrides !== 'object') throw new Error() } catch { return <p className="mt-3 text-xs text-rose-700">参数覆盖必须是 JSON 对象。请在高级编辑器中修正。</p> }
  const commit = (parameter: Record<string, unknown>, raw: string) => {
    const key = String(parameter.key)
    const next = { ...overrides }
    if (raw === '') delete next[key]
    else {
      try {
        const type = String(parameter.type)
        let parsed: unknown = raw
        if (['integer', 'decimal'].includes(type)) { parsed = Number(raw); if (!Number.isFinite(parsed) || (type === 'integer' && !Number.isInteger(parsed))) throw new Error('请输入有效数字') }
        else if (['json', 'object_ref', 'object_set', 'boolean'].includes(type)) parsed = JSON.parse(raw)
        next[key] = parsed
      } catch { setError(`${parameter.label || key} 的格式无效，请修正后再保存。`); return }
    }
    setError(''); onChange(JSON.stringify(next, null, 2))
  }
  return <div className="mt-3"><p className="mb-2 text-xs text-slate-500">输入覆盖值；留空使用模型的初始值。</p><div className="grid gap-3 md:grid-cols-3">{parameters.map(parameter => {
    const key = String(parameter.key), overridden = Object.hasOwn(overrides, key), current = overridden ? renderValue(overrides[key]) : ''
    const className = `wb-input mt-1 w-full ${overridden ? 'bg-blue-50 text-blue-700' : ''}`
    return <label key={`${key}:${current}`} className="text-xs">{String(parameter.label || key)} {String(parameter.unit || '')}{parameter.type === 'boolean' ? <select aria-label={`覆盖 ${parameter.label || key}`} className={className} value={current} onChange={event => commit(parameter, event.target.value)}><option value="">初始值：{renderValue(parameter.default)}</option><option value="true">是</option><option value="false">否</option></select> : ['json', 'object_ref', 'object_set'].includes(String(parameter.type)) ? <textarea aria-label={`覆盖 ${parameter.label || key}`} className={`${className} min-h-16 font-mono`} defaultValue={current} placeholder={renderValue(parameter.default)} onBlur={event => commit(parameter, event.target.value)} /> : <input aria-label={`覆盖 ${parameter.label || key}`} className={className} defaultValue={current} placeholder={renderValue(parameter.default)} onBlur={event => commit(parameter, event.target.value)} />}</label>
  })}</div>{error && <p role="alert" className="mt-2 text-xs text-rose-700">{error}</p>}</div>
}
