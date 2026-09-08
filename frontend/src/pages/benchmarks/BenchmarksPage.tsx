import { useState } from 'react'
import { constructionApi } from '@/api/construction'

export default function BenchmarksPage() {
  const [ontologyId, setOntologyId] = useState('')
  const [benchmark, setBenchmark] = useState('OSKGC')
  const [predicted, setPredicted] = useState('[{"id":"equipment-1","type":"Equipment"}]')
  const [gold, setGold] = useState('[{"id":"equipment-1","type":"Equipment"}]')
  const [result, setResult] = useState<any>(null)
  const [error, setError] = useState('')
  const run = async () => {
    setError('')
    try {
      const response = await constructionApi.benchmark({
        ontology_id: ontologyId,
        benchmark,
        predicted_entities: JSON.parse(predicted),
        gold_entities: JSON.parse(gold),
        predicted_triples: [],
        gold_triples: [],
      })
      setResult(response)
    } catch (e: any) { setError(e?.detail || e?.message || '评测失败') }
  }
  return <div className="max-w-4xl">
    <h2 className="text-xl font-semibold mb-2">评测实验</h2>
    <p className="text-sm text-gray-500 mb-6">使用 gold 数据对实体、三元组和 Schema 符合度进行可复现比较；置信度不替代准确率。</p>
    <div className="bg-white border rounded-lg p-5 space-y-4">
      <label className="block text-sm">Ontology ID<input value={ontologyId} onChange={e => setOntologyId(e.target.value)} className="mt-1 w-full border rounded p-2" placeholder="粘贴本体 ID" /></label>
      <label className="block text-sm">基准<select value={benchmark} onChange={e => setBenchmark(e.target.value)} className="mt-1 border rounded p-2 ml-2"><option>OSKGC</option><option>CQ4OE</option><option>custom-gold</option></select></label>
      <div className="grid grid-cols-2 gap-4"><label className="text-sm">预测 JSON<textarea value={predicted} onChange={e => setPredicted(e.target.value)} className="block mt-1 w-full h-28 border rounded p-2 font-mono text-xs" /></label><label className="text-sm">Gold JSON<textarea value={gold} onChange={e => setGold(e.target.value)} className="block mt-1 w-full h-28 border rounded p-2 font-mono text-xs" /></label></div>
      <button disabled={!ontologyId} onClick={run} className="bg-black text-white px-4 py-2 rounded disabled:opacity-40">运行评测</button>
      {error && <p className="text-red-600 text-sm">{error}</p>}
    </div>
    {result && <pre className="mt-5 bg-gray-900 text-green-200 rounded p-4 text-xs overflow-auto">{JSON.stringify(result, null, 2)}</pre>}
  </div>
}
