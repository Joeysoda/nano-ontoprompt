import { useState } from 'react'
import { apiClientV2, formatApiError } from '@/api/client'

const pretty = (value: unknown) => JSON.stringify(value, null, 2)

export default function StudyPackagePanel({ ontologyId, studyId, version, onInstalled }: {
  ontologyId: string; studyId: string; version: number; onInstalled: () => void
}) {
  const [packageText, setPackageText] = useState('')
  const [bindingsText, setBindingsText] = useState('{\n  "types": {},\n  "properties": {}\n}')
  const [name, setName] = useState('')
  const [report, setReport] = useState<Record<string, unknown> | null>(null)
  const [notice, setNotice] = useState('')
  const [busy, setBusy] = useState(false)
  const payload = () => ({ package: JSON.parse(packageText), bindings: JSON.parse(bindingsText) })
  const run = async (action: 'inspect' | 'install') => {
    setBusy(true); setNotice('')
    try {
      const body = payload()
      if (action === 'inspect') {
        const result = await apiClientV2.post<Record<string, unknown>>(`/ontologies/${ontologyId}/scenario-study-packages:inspect`, body)
        setReport(result); setNotice(result.valid ? '目标本体兼容，可以安装。' : '存在未满足的依赖或绑定。')
      } else {
        await apiClientV2.post(`/ontologies/${ontologyId}/scenario-study-packages:install`, { ...body, name: name.trim() || null })
        setNotice('应用包已安装为草稿，请检查并发布。'); onInstalled()
      }
    } catch (error) { setNotice(formatApiError(error)) } finally { setBusy(false) }
  }
  return <details className="mt-4 border-t pt-4 text-xs">
    <summary className="cursor-pointer font-medium">跨本体复用 · 导出、重绑定与安装</summary>
    <div className="mt-3 grid gap-3 lg:grid-cols-2">
      <section><p className="mb-2 text-slate-500">导出当前已发布版本，不包含数据、运行历史、凭证或用户 Function 源码。</p><button className="wb-button-secondary" disabled={!version || busy} onClick={async () => {
        setBusy(true); setNotice('')
        try { const value = await apiClientV2.get(`/ontologies/${ontologyId}/scenario-study-definitions/${studyId}/versions/${version}:export`); setPackageText(pretty(value)); setNotice('已导出不可变应用包。可复制到目标本体后重绑定。') }
        catch (error) { setNotice(formatApiError(error)) } finally { setBusy(false) }
      }}>导出 v{version || '—'}</button><textarea aria-label="Study 应用包 JSON" className="wb-input mt-2 min-h-52 w-full font-mono" value={packageText} onChange={event => { setPackageText(event.target.value); setReport(null) }} /></section>
      <section><p className="mb-2 text-slate-500">在目标本体页面粘贴应用包，将源类型和属性逐项绑定到目标 API 名称。</p><textarea aria-label="Study 应用包绑定 JSON" className="wb-input min-h-40 w-full font-mono" value={bindingsText} onChange={event => { setBindingsText(event.target.value); setReport(null) }} /><label className="mt-2 block">安装后的名称<input aria-label="安装后的研究名称" className="wb-input mt-1 w-full" value={name} onChange={event => setName(event.target.value)} placeholder="留空沿用应用包名称" /></label><div className="mt-2 flex gap-2"><button className="wb-button-secondary" disabled={!packageText || busy} onClick={() => void run('inspect')}>预检目标本体</button><button className="wb-button-primary" disabled={!packageText || busy || report?.valid !== true} onClick={() => void run('install')}>安装为草稿</button></div></section>
    </div>
    {notice && <p role="status" className="mt-3 text-slate-700">{notice}</p>}
    {report && <pre aria-label="应用包预检结果" className="mt-2 max-h-60 overflow-auto rounded bg-slate-950 p-3 text-slate-100">{pretty(report)}</pre>}
  </details>
}
