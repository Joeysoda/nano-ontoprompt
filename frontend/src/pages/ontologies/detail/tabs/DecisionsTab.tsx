import { useEffect, useRef, useState } from 'react';
import cytoscape from 'cytoscape';
import { apiClientV2 } from '@/api/client';

// Chain cards and labelled connectors follow Semantica DecisionWorkspace
// (MIT, commit 3a69721); data and authentication use the host workbench.
const operations = {chain:'上下游链', influenced:'受影响决策', precedents:'先例链', roots:'上游根源', loops:'决策环路', score:'影响评分', network:'网络统计', time:'按记录时间追踪', distance:'两决策路径'};
type Decision = {id:string; scenario:string; outcome:string; reasoning:string; test_record:boolean; entity_ids:string[]; evidence:any[]; category?:string; decision_maker?:string; valid_from?:string; valid_until?:string; policy_ids?:string[]; approval_chain?:any[]};
type Link = {id:string; source:string; target:string; relationship:string; explanation:string};
const control = 'rounded border p-2 text-sm';

export default function DecisionsTab({ontologyId}:{ontologyId:string}) {
  const base = `/ontologies/${ontologyId}/decisions`;
  const [decisions,setDecisions] = useState<Decision[]>([]);
  const [links,setLinks] = useState<Link[]>([]);
  const [selected,setSelected] = useState('');
  const [target,setTarget] = useState('');
  const [scenario,setScenario] = useState('');
  const [reasoning,setReasoning] = useState('');
  const [category,setCategory] = useState('maintenance');
  const [outcome,setOutcome] = useState('');
  const [maker,setMaker] = useState('');
  const [validFrom,setValidFrom] = useState('');
  const [validUntil,setValidUntil] = useState('');
  const [policies,setPolicies] = useState('');
  const [approvals,setApprovals] = useState('');
  const [entity,setEntity] = useState('');
  const [run,setRun] = useState('');
  const [conclusion,setConclusion] = useState('');
  const [test,setTest] = useState(true);
  const [kind,setKind] = useState('INFLUENCED');
  const [explanation,setExplanation] = useState('');
  const [operation,setOperation] = useState('chain');
  const [direction,setDirection] = useState('downstream');
  const [depth,setDepth] = useState(5);
  const [at,setAt] = useState('');
  const [result,setResult] = useState<any>(null);
  const [error,setError] = useState('');
  const [busy,setBusy] = useState(false);
  const [runs,setRuns] = useState<any[]>([]);
  const [proofs,setProofs] = useState<any[]>([]);
  const [objects,setObjects] = useState<any[]>([]);
  const canvas = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);
  useEffect(()=>{
    if(!canvas.current) return;
    const cy=cytoscape({container:canvas.current,
      elements:[...decisions.map(d=>({data:{id:d.id,label:d.scenario}})),...links.map(l=>({data:{id:l.id,source:l.source,target:l.target,label:l.relationship}}))],
      style:[{selector:'node',style:{label:'data(label)','background-color':'#64748b','font-size':12,'text-wrap':'wrap','text-max-width':'160px'}},{selector:'edge',style:{label:'data(label)','curve-style':'bezier','target-arrow-shape':'triangle','line-color':'#8b5cf6','target-arrow-color':'#8b5cf6','font-size':10}},{selector:'.impact-highlight',style:{'background-color':'#2563eb','line-color':'#2563eb','target-arrow-color':'#2563eb','width':5,'opacity':1}},{selector:'.impact-dim',style:{opacity:0.18}},{selector:':selected',style:{'background-color':'#7c3aed','line-color':'#7c3aed'}}],
      layout:{name:'breadthfirst',directed:true,padding:45},minZoom:0.2,maxZoom:3});
    cyRef.current=cy;
    cy.on('tap','node',e=>setSelected(e.target.id()));
    return ()=>{cy.destroy(); cyRef.current=null;};
  },[decisions,links]);
  useEffect(()=>{
    const cy=cyRef.current;
    if(!cy) return;
    cy.elements().removeClass('impact-highlight impact-dim');
    const ids=new Set((result?.highlight?.nodes || []) as string[]);
    const edgeIds=new Set((result?.highlight?.edges || []).map((e:any)=>e.id));
    if(!ids.size) return;
    cy.nodes().forEach(n=>{n.toggleClass('impact-highlight',ids.has(n.id())).toggleClass('impact-dim',!ids.has(n.id()));});
    cy.edges().forEach(e=>{e.toggleClass('impact-highlight',edgeIds.has(e.id())).toggleClass('impact-dim',!edgeIds.has(e.id()));});
  },[result,decisions,links]);
  const load = async () => {const data:any = await apiClientV2.get(base); setDecisions(data.decisions); setLinks(data.links);};
  useEffect(()=>{setSelected(''); setResult(null); load().catch(e=>setError(e.message || '加载失败'));},[ontologyId]);
  useEffect(()=>{
    let active=true;
    Promise.all([apiClientV2.get(`/ontologies/${ontologyId}/reasoning/runs`),apiClientV2.get(`/ontologies/${ontologyId}/reasoning/graph`)]).then(([history,graph]:any[])=>{if(active){setRuns(history);setObjects(graph.nodes || []);}}).catch(e=>{if(active)setError(e.message || '依据选择列表加载失败');});
    return ()=>{active=false;};
  },[ontologyId]);
  useEffect(()=>{
    let active=true;setProofs([]);setConclusion('');
    if(run) apiClientV2.get(`/ontologies/${ontologyId}/reasoning/runs/${run}`).then((r:any)=>{if(active)setProofs(r.inferred_facts || []);}).catch(e=>{if(active)setError(e.message || '推理记录加载失败');});
    return ()=>{active=false;};
  },[run,ontologyId]);
  const act = async (fn:()=>Promise<void>) => {setBusy(true);setError('');try{await fn();}catch(e:any){setError(typeof e.detail==='string'?e.detail:e.message || '请求失败');}finally{setBusy(false);}};
  const chosen = decisions.find(d=>d.id===selected);
  const options = decisions.map(d=><option key={d.id} value={d.id}>{d.scenario}</option>);
  return <div className="space-y-4">
    <header className="rounded border bg-white p-5"><div className="flex flex-wrap items-start justify-between gap-4"><div><p className="text-xs uppercase tracking-wide text-slate-500">Semantica DecisionWorkspace · 适配版</p><h2 className="mt-1 text-xl font-semibold text-slate-900">决策与影响链工作台</h2><p className="mt-1 text-sm text-slate-600">记录决策、关联业务对象，并沿有向关系分析上游、下游、先例和影响路径。</p></div><div className="grid grid-cols-3 gap-2 text-center text-xs"><div className="rounded bg-slate-50 px-4 py-2"><div className="text-lg font-semibold text-slate-900">{decisions.length}</div><div className="text-slate-500">决策</div></div><div className="rounded bg-slate-50 px-4 py-2"><div className="text-lg font-semibold text-slate-900">{links.length}</div><div className="text-slate-500">有向关系</div></div><div className="rounded bg-slate-50 px-4 py-2"><div className="text-lg font-semibold text-slate-900">{result?.highlight?.nodes?.length || 0}</div><div className="text-slate-500">当前路径节点</div></div></div></div></header>
    <p className="rounded border bg-white p-4">记录决策及依据，追踪已记录的关系。测试决策不代表实际执行；图评分不等于故障概率。</p>
    {error && <p role="alert" className="text-red-700">{error}</p>}
    <section className="rounded border bg-white p-4 space-y-3">
      <h2>创建决策</h2>
      <div className="grid gap-2 md:grid-cols-2"><input aria-label="决策内容" className={control} placeholder="例如：安排刀具检查" value={scenario} onChange={e=>setScenario(e.target.value)}/><input aria-label="决策类别" className={control} placeholder="决策类别" value={category} onChange={e=>setCategory(e.target.value)}/></div>
      <div className="grid gap-2 md:grid-cols-2"><input aria-label="决策理由" className={control} placeholder="决策理由" value={reasoning} onChange={e=>setReasoning(e.target.value)}/><input aria-label="决策结果" className={control} placeholder="决策结果" value={outcome} onChange={e=>setOutcome(e.target.value)}/></div>
      <details><summary className="cursor-pointer text-sm font-medium">高级决策字段</summary><div className="mt-2 grid gap-2 md:grid-cols-2"><input aria-label="决策人" className={control} placeholder="决策人" value={maker} onChange={e=>setMaker(e.target.value)}/><input aria-label="政策ID" className={control} placeholder="政策 ID，逗号分隔" value={policies} onChange={e=>setPolicies(e.target.value)}/><input aria-label="有效开始" className={control} placeholder="有效开始 ISO 时间" value={validFrom} onChange={e=>setValidFrom(e.target.value)}/><input aria-label="有效结束" className={control} placeholder="有效结束 ISO 时间" value={validUntil} onChange={e=>setValidUntil(e.target.value)}/><textarea aria-label="审批链" className={control} placeholder='审批链 JSON，例如 [{"approver":"主管","status":"approved"}]' value={approvals} onChange={e=>setApprovals(e.target.value)}/></div></details>
      <select aria-label="业务对象ID" className={control} value={entity} onChange={e=>setEntity(e.target.value)}><option value="">关联业务对象（可选，当前图样本）</option>{objects.map(n=><option key={n.id} value={n.id}>{n.entity_type}: {n.properties?.name || n.id}</option>)}</select>
      <select aria-label="推理运行ID" className={control} value={run} onChange={e=>setRun(e.target.value)}><option value="">选择推理记录（可选）</option>{runs.map(r=><option key={r.run_id} value={r.run_id}>{r.created_at} · {r.status} · {r.derived_fact_count} 条</option>)}</select>
      <select aria-label="依据结论" className={control} value={conclusion} onChange={e=>setConclusion(e.target.value)}><option value="">选择该次运行的结论</option>{proofs.map(p=><option key={p.conclusion} value={p.conclusion}>{p.conclusion}</option>)}</select>
      <label><input type="checkbox" checked={test} onChange={e=>setTest(e.target.checked)}/>测试决策</label>
      <button className={control} disabled={busy || !scenario || Boolean(run)!==Boolean(conclusion)} onClick={()=>act(async()=>{let approval_chain=[];try{approval_chain=approvals?JSON.parse(approvals):[];}catch{throw new Error('审批链必须是 JSON');}const d:any=await apiClientV2.post(base,{category,scenario,reasoning,outcome,decision_maker:maker,valid_from:validFrom||null,valid_until:validUntil||null,policy_ids:policies.split(',').map(x=>x.trim()).filter(Boolean),approval_chain,entity_ids:entity?[entity]:[],basis:run?[{run_id:run,conclusion}]:[],test_record:test}); await load();setSelected(d.id);setScenario('');})}>保存决策</button>
    </section>
    <section className="rounded border bg-white p-4 space-y-3">
      <h2>选择与关联决策</h2>
      <select aria-label="起点决策" className={control} value={selected} onChange={e=>{setSelected(e.target.value);setResult(null);}}><option value="">选择起点</option>{options}</select>
      <select aria-label="目标决策" className={control} value={target} onChange={e=>setTarget(e.target.value)}><option value="">选择目标</option>{options}</select>
      <select className={control} aria-label="关系类型" value={kind} onChange={e=>setKind(e.target.value)}>{['INFLUENCED','CAUSED','PRECEDENT_FOR'].map(k=><option key={k}>{k}</option>)}</select>
      <input className={control} aria-label="关系依据" placeholder="为什么建立此关系？" value={explanation} onChange={e=>setExplanation(e.target.value)}/>
      <button className={control} disabled={busy || !selected || !target || !explanation} onClick={()=>act(async()=>{await apiClientV2.post(base+'/links',{source:selected,target,relationship:kind,explanation});await load();})}>保存关联</button>
      {chosen && <article className="border-l-4 border-violet-500 p-3"><h3>{chosen.scenario} {chosen.test_record?'（测试）':''}</h3><p>{chosen.reasoning}</p><p>涉及对象：{chosen.entity_ids.join(', ') || '未关联'}</p><details><summary>查看推理依据及证据</summary>{chosen.evidence.map((p:any,i:number)=><div key={i} className="my-3 rounded border p-3"><p>{p.conclusion}</p><p>命中规则：{p.rule}</p><p className="text-xs">run_id：{p.run_id} · 关联时状态：{p.run_status_at_link}</p><p>直接前提：{p.premises?.join('；')}</p>{p.evidence?.map((e:any,j:number)=><details key={j}><summary>{e.source_file || '未定位原始文件'} · 行 {e.source_row ?? '未知'}</summary><p>证据 ID：{e.evidence_ref_id || '缺失'}</p><p>数据版本：{e.source_version || '未知'}</p><pre className="whitespace-pre-wrap text-xs">{e.evidence_text || '历史记录没有原始证据，不代表没有推理前提'}</pre></details>)}</div>)}{!chosen.evidence.length && <p>未关联推理依据</p>}</details></article>}
    </section>
    <section className="rounded border bg-white p-4 space-y-3">
      <h2>决策链分析</h2>
      <select aria-label="分析类型" className={control} value={operation} onChange={e=>{setOperation(e.target.value);setResult(null);}}>{Object.entries(operations).map(([k,v])=><option key={k} value={k}>{v}</option>)}</select>
      <select aria-label="方向" className={control} value={direction} onChange={e=>setDirection(e.target.value)}><option value="downstream">下游</option><option value="upstream">上游</option></select>
      <input aria-label="最大深度" className={control} type="number" min={1} max={20} value={depth} onChange={e=>setDepth(Number(e.target.value))}/>
      {operation==='time' && <input aria-label="截止时间" className={control} type="datetime-local" value={at} onChange={e=>setAt(e.target.value)}/>}
      <button disabled={busy} className={control} onClick={()=>act(async()=>{setResult(null);setResult(await apiClientV2.post(base+'/analyze',{operation,decision_id:selected || null,target_id:target || null,direction,max_depth:depth,at_time:at?new Date(at).toISOString():null}));})}>{busy?'分析中…':'运行分析'}</button>
      {result && <><p>{result.score_notice}</p>{result.highlight && <div data-testid="highlight-status" className="rounded border border-blue-200 bg-blue-50 p-3 text-sm text-blue-900"><strong>已高亮影响链</strong>：{result.highlight.nodes.length} 个决策节点，{result.highlight.edges.length} 条有向关系。蓝色为本次分析路径，灰显为未参与本次路径的关系。</div>}{Array.isArray(result.result) && <div>{!result.result.length && <p>在本次查询范围内未找到结果</p>}{result.result.filter((r:any)=>r.decision_id).map((r:any,i:number)=><article key={i} className="my-2 rounded border-l-4 border-violet-500 bg-violet-50 p-3"><button onClick={()=>setSelected(r.decision_id)}>{r.scenario || r.decision_id}</button><p className="text-xs">{JSON.stringify(r.metadata)}</p></article>)}</div>}{result.result?.causal_path && <div className="rounded border bg-slate-50 p-3"><h3 className="font-medium">因果路径</h3><div className="mt-2 flex flex-wrap items-center gap-2">{result.result.causal_path.map((id:string,i:number)=><span key={id} className="flex items-center gap-2"><button className="rounded border bg-white px-2 py-1 text-sm hover:border-blue-500" onClick={()=>setSelected(id)}>{decisions.find(d=>d.id===id)?.scenario || id}</button>{i < result.result.causal_path.length-1 && <span className="font-bold text-violet-600">→</span>}</span>)}</div><p className="mt-2 text-xs text-slate-600">共 {result.result.causal_hop_count} 跳 · 置信度衰减 {result.result.confidence_decay}</p></div>}<details open><summary>完整分析结果</summary><pre data-testid="decision-analysis-result" className="max-h-96 overflow-auto whitespace-pre-wrap text-xs">{JSON.stringify(result.result,null,2)}</pre></details></>}
    </section>
    <section className="rounded border bg-white p-4"><div className="flex flex-wrap items-center justify-between gap-2"><div><h2>已记录的有向关系</h2><p className="text-sm text-slate-500">本体内决策总览；点击节点查看依据。分析结果会在图中高亮。</p></div><div className="flex gap-3 text-xs text-slate-600"><span><i className="mr-1 inline-block h-3 w-3 rounded-full bg-blue-600"/>分析路径</span><span><i className="mr-1 inline-block h-3 w-3 rounded-full bg-violet-500"/>已记录关系</span><span><i className="mr-1 inline-block h-3 w-3 rounded-full bg-slate-300"/>未参与路径</span></div></div><div ref={canvas} data-testid="decision-graph" className="mt-3 h-96 rounded border bg-slate-50"/>{!links.length && <p>暂无决策关联</p>}{links.map(l=><article key={l.id} className="my-3 border-l-4 border-violet-500 p-3"><button onClick={()=>setSelected(l.source)}>{decisions.find(d=>d.id===l.source)?.scenario}</button><p className="text-violet-700">↓ {l.relationship} · {l.explanation}</p><button onClick={()=>setSelected(l.target)}>{decisions.find(d=>d.id===l.target)?.scenario}</button></article>)}</section>
  </div>;
}
