import { useRef, useState } from 'react'
import { FileImage, FileText, Film, Loader2, UploadCloud } from 'lucide-react'
import { apiClientV2 } from '@/api/client'

type UploadResult = { id?: string; name?: string; status?: string; [key: string]: unknown }

/** Evidence-first multimodal intake. It deliberately does not claim image
 * understanding until MiniMax M3 is configured and a processor is run. */
export default function MultimodalDataPage() {
  const inputRef = useRef<HTMLInputElement>(null)
  const [file, setFile] = useState<File | null>(null)
  const [result, setResult] = useState<UploadResult | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState('')

  const upload = async () => {
    if (!file) return
    setBusy(true); setError(''); setResult(null)
    try {
      const form = new FormData(); form.append('file', file)
      const data = await apiClientV2.post<UploadResult>('/datasets/upload', form)
      setResult(data)
    } catch (err: any) {
      setError(err?.detail || err?.message || '上传失败')
    } finally { setBusy(false) }
  }

  return (
    <div className="space-y-6 max-w-5xl">
      <div>
        <h2 className="text-xl font-semibold">多模态数据</h2>
        <p className="text-sm text-gray-500 mt-1">上传图片、文档、表格或视频，先登记原文件，再提取文本和证据关联。</p>
      </div>
      <div className="grid md:grid-cols-4 gap-3">
        {[
          ['上传文件', UploadCloud, '选择原始媒体并保存来源。'],
          ['提取内容', FileText, 'OCR、PDF/DOCX 文本或视频抽帧。'],
          ['识别实体', FileImage, '只生成带证据的候选实体。'],
          ['关联图谱', Film, '明确 ID/元数据匹配后再建边。'],
        ].map(([title, Icon, text]) => {
          const Component = Icon as typeof UploadCloud
          return <div key={title as string} className="bg-white border rounded-xl p-4"><Component size={17} className="text-gray-500" /><p className="font-medium text-sm mt-3">{title as string}</p><p className="text-xs text-gray-500 mt-1 leading-5">{text as string}</p></div>
        })}
      </div>
      <div className="bg-white border rounded-xl p-5 space-y-4">
        <div className="flex items-center justify-between"><h3 className="font-medium">1. 上传并登记原文件</h3><span className="text-xs rounded-full border px-2 py-1 text-amber-700 bg-amber-50">MiniMax M3：待配置</span></div>
        <input ref={inputRef} type="file" accept=".csv,.json,.xlsx,.xls,.pdf,.docx,.png,.jpg,.jpeg,.mp4,.mov" className="hidden" onChange={e => { setFile(e.target.files?.[0] || null); setResult(null); setError('') }} />
        <button onClick={() => inputRef.current?.click()} className="w-full border-2 border-dashed rounded-xl p-8 text-sm text-gray-500 hover:border-gray-500 hover:text-black transition"><UploadCloud size={22} className="mx-auto mb-2" />点击选择 CSV、PDF、图片或视频</button>
        {file && <div className="flex items-center justify-between border rounded-lg px-3 py-2 text-sm"><span className="truncate">{file.name}</span><span className="text-xs text-gray-400">{(file.size / 1024 / 1024).toFixed(2)} MB</span></div>}
        <button onClick={upload} disabled={!file || busy} className="px-4 py-2 bg-black text-white rounded-lg text-sm disabled:opacity-40 flex items-center gap-2">{busy && <Loader2 size={14} className="animate-spin" />}上传并登记</button>
        {error && <p className="text-sm text-red-600">{error}</p>}
        {result && <pre className="text-xs bg-gray-50 border rounded-lg p-3 overflow-auto">{JSON.stringify(result, null, 2)}</pre>}
      </div>
      <div className="border rounded-xl bg-gray-50 p-4 text-xs text-gray-600 leading-5"><strong>当前边界：</strong>没有 MiniMax M3 或可定位证据时，系统只保存媒体和提取片段，不会让模型猜测设备关系；视频在本阶段只做抽帧入口，不宣称理解视频语义。</div>
    </div>
  )
}
