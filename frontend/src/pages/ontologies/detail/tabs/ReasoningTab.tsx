import { useCallback, useEffect, useRef, useState } from "react";
import cytoscape from "cytoscape";
import { apiClientV2 } from "@/api/client";

type Evidence = { fact: string; source: string; source_file?: string; source_row?: string; source_version?: string; evidence_ref_id?: string; evidence_text?: string; content_hash?: string };
type Inference = { conclusion: string; rule: string; premises: string[]; evidence: Evidence[] };
type Result = { run_id: string; status: string; facts: string[]; rules: string[]; inferred_facts: Inference[]; rules_fired: string[]; graph: { status: string; written: number; unary_facts?: number; reason?: string; missing_nodes?: string[] } };
type Run = { run_id: string; status: string; created_at: string; derived_fact_count: number };
type GraphData = { available: boolean; total_instances: number; nodes: Array<{ id: string; entity_type: string; properties: Record<string, any> }>; edges: Array<{ id: string; source: string; target: string; label: string; properties: Record<string, any> }> };
const button = "rounded-lg border border-slate-300 px-3 py-2 text-sm disabled:opacity-50";
const message = (e: any) => typeof e?.detail === "string" ? e.detail : e?.detail?.message || e?.message || "请求失败";

export default function ReasoningTab({ ontologyId }: { ontologyId: string }) {
  const base = `/ontologies/${ontologyId}/reasoning`;
  const [facts, setFacts] = useState("");
  const [rules, setRules] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [notice, setNotice] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  const [runs, setRuns] = useState<Run[]>([]);
  const [graph, setGraph] = useState<GraphData | null>(null);
  const [selected, setSelected] = useState<Inference | null>(null);
  const [nodeDetails, setNodeDetails] = useState<Record<string, any> | null>(null);
  const [derivedOnly, setDerivedOnly] = useState(false);
  const [query, setQuery] = useState("");
  const canvas = useRef<HTMLDivElement>(null);
  const cy = useRef<cytoscape.Core | null>(null);

  const refresh = useCallback(async () => {
    const [history, data] = await Promise.all([
      apiClientV2.get<Run[]>(`${base}/runs`),
      apiClientV2.get<GraphData>(`${base}/graph`),
    ]);
    setRuns(history); setGraph(data);
    return history;
  }, [base]);

  useEffect(() => {
    let active = true;
    setResult(null); setSelected(null); setError("");
    (async () => {
      const [inputs, history] = await Promise.all([
        apiClientV2.get<{facts: string[]; rules: string[]; truncated: boolean}>(`${base}/inputs`),
        refresh(),
      ]);
      if (!active) return;
      setFacts(inputs.facts.join("\n")); setRules(inputs.rules.join("\n"));
      setNotice(inputs.facts.length ? `已加载 ${inputs.facts.length} 条原始图事实；不含已保存的派生边。${inputs.truncated ? "注意：仅前 500 个实例。" : ""}` : "当前本体没有实例，请先导入数据。");
      const latest = history.find(r => r.status === "applied") || history[0];
      if (latest) {
        const saved = await apiClientV2.get<Result>(`${base}/runs/${latest.run_id}`);
        if (active) setResult(saved);
      }
    })().catch(e => active && setError(message(e)));
    return () => { active = false; };
  }, [base, refresh]);

  async function loadInputs() {
    setBusy(true); setError("");
    try {
      const data = await apiClientV2.get<{facts: string[]; rules: string[]; truncated: boolean}>(`${base}/inputs`);
      setFacts(data.facts.join("\n")); setRules(data.rules.join("\n"));
      setResult(null); setSelected(null);
      setNotice(`已加载 ${data.facts.length} 条原始图事实${data.truncated ? "（前 500 个实例的子图）" : ""}；规则来自当前本体的可执行规则定义。`);
    } catch(e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function run() {
    setBusy(true); setError(""); setSelected(null);
    try {
      const data = await apiClientV2.post<Result>(`${base}/run`, {
        facts: facts.split(/\r?\n/).map(x => x.trim()).filter(Boolean),
        rules: rules.split(/\r?\n/).map(x => x.trim()).filter(Boolean),
      });
      setResult(data);
      setNotice("推理完成：预览及证据已保存为运行记录，尚未写入实例图。");
      await refresh();
    } catch(e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function save() {
    if (!result) return;
    setBusy(true); setError("");
    try {
      const data = await apiClientV2.post<Result>(`${base}/runs/${result.run_id}/apply`);
      setResult(data);
      setNotice(data.status === "applied" ? `结果已保存；${data.graph.written} 条二元关系投影到图。刷新页面后仍可查看。` : "投影未完成；预览记录保留，可修正后重试。");
      await refresh();
    } catch(e) { setError(message(e)); } finally { setBusy(false); }
  }
  async function restore(id: string) {
    if (!id) return;
    setBusy(true); setError("");
    try {
      const data = await apiClientV2.get<Result>(`${base}/runs/${id}`);
      setResult(data); setSelected(null); setFacts(data.facts.join("\n")); setRules(data.rules.join("\n"));
    } catch(e) { setError(message(e)); } finally { setBusy(false); }
  }

  useEffect(() => {
    if (!canvas.current || !graph) return;
    const persistedEdges = graph.edges.filter(e => (!derivedOnly || e.properties?.derived) &&
      (!e.properties?.derived || e.properties.reasoning_run_id === result?.run_id));
    const knownIds = new Set(graph.nodes.map(n => n.id));
    const previewFacts = result && result.status !== "applied" ? result.inferred_facts : [];
    const previewEdges = previewFacts.flatMap(item => {
      const match = item.conclusion.match(/^([A-Za-z_][A-Za-z0-9_]*)\(([^,]+),([^\)]+)\)$/);
      if (!match) return [];
      const source = match[2].trim(); const target = match[3].trim();
      if (!knownIds.has(source) || !knownIds.has(target)) return [];
      return [{ id: `preview:${item.conclusion}`, source, target, type: "INFERRED_PREVIEW", label: match[1], properties: { preview: true, derived: true, conclusion: item.conclusion, predicate: match[1], premises: item.premises } }];
    });
    const edges = [...persistedEdges, ...previewEdges].filter(e => !derivedOnly || e.properties?.derived);
    const ids = new Set(edges.flatMap(e => [e.source, e.target]));
    const nodes = derivedOnly ? graph.nodes.filter(n => ids.has(n.id)) : graph.nodes;
    const instance = cytoscape({
      container: canvas.current,
      elements: [
        ...nodes.map(n => ({ data: { id: n.id, label: `${n.entity_type}\n${n.id.slice(-8)}`, ...n.properties } })),
        ...edges.map(e => ({ data: { ...e, derived: Boolean(e.properties?.derived), preview: Boolean(e.properties?.preview) } })),
      ],
      style: [
        { selector: "node", style: { label: "data(label)", "font-size": 9, "text-wrap": "wrap", "background-color": "#64748b", width: 20, height: 20 } },
        { selector: "edge", style: { width: 1, "line-color": "#cbd5e1", "target-arrow-color": "#cbd5e1", "target-arrow-shape": "triangle", "curve-style": "bezier" } },
        { selector: "edge[?derived]", style: { width: 2, "line-color": "#7c3aed", "target-arrow-color": "#7c3aed" } },
        { selector: "edge[?preview]", style: { width: 2, "line-color": "#7c3aed", "target-arrow-color": "#7c3aed", "line-style": "dashed", "line-dash-pattern": [8, 5] } },
        { selector: ":selected", style: { "background-color": "#f59e0b", "line-color": "#f59e0b", "target-arrow-color": "#f59e0b", label: "data(label)", "font-size": 10 } },
      ],
      layout: { name: "cose", animate: false, nodeRepulsion: () => 15000, idealEdgeLength: () => 70 },
      minZoom: 0.05, maxZoom: 4,
    });
    instance.on("tap", "edge", event => {
      const edge = event.target.data();
      setNodeDetails({ relation: edge.label, source: edge.source, target: edge.target, ...edge.properties });
      setSelected(result?.inferred_facts.find(f => f.conclusion === edge.properties?.conclusion) || null);
    });
    instance.on("tap", "node", event => setNodeDetails(event.target.data()));
    cy.current = instance;
    return () => { instance.destroy(); cy.current = null; };
  }, [graph, derivedOnly, result]);

  function focus(item: Inference) {
    setSelected(item);
    const edge = cy.current?.edges().filter(e => e.data("properties")?.conclusion === item.conclusion);
    if (edge?.length) { cy.current?.elements().unselect(); edge.select(); cy.current?.fit(edge.closedNeighborhood(), 70); }
  }
  function jumpToGraph() {
    canvas.current?.scrollIntoView({ behavior: "smooth", block: "center" });
  }
  const visible = result?.inferred_facts.filter(f => f.conclusion.toLowerCase().includes(query.toLowerCase())) || [];
  return <div className="space-y-5">
    <div className="rounded-xl border bg-white p-4 text-sm text-slate-600">
      加载实例事实 → 执行规则 → 预览派生事实 → 查看证据 → 保存结果 → 实例图展示。
      <p className="mt-1 text-xs">这是规则关系推导，不是故障预测或 What-if 仿真。证据展示一条真实命中的推导路径，不宣称穷举全部证明。</p>
    </div>
    {error && <div role="alert" className="rounded-lg border border-red-200 bg-red-50 p-3 text-red-700">{error}</div>}
    {notice && <p role="status" className="text-sm text-slate-600">{notice}</p>}
    <div className="grid gap-5 xl:grid-cols-2">
      <section className="space-y-4 rounded-xl border bg-white p-5">
        <h2 className="font-semibold">推理验证</h2>
        <button className={button} disabled={busy} onClick={loadInputs}>加载当前图事实与规则</button>
        <label className="block text-sm">Facts<textarea aria-label="Facts" rows={7} value={facts} onChange={e => {setFacts(e.target.value); setResult(null); setSelected(null);}} className="mt-2 w-full rounded-lg border p-3 font-mono text-xs" /></label>
        <label className="block text-sm">Rules<textarea aria-label="Rules" rows={5} value={rules} onChange={e => {setRules(e.target.value); setResult(null); setSelected(null);}} className="mt-2 w-full rounded-lg border p-3 font-mono text-xs" /></label>
        <button className={button + " bg-slate-900 text-white"} disabled={busy || !facts || !rules} onClick={run}>{busy ? "处理中…" : "运行推理"}</button>
      </section>
      <section className="space-y-3 rounded-xl border bg-white p-5">
        <label className="block text-sm">历史运行（刷新后可恢复）<select aria-label="历史运行" className="mt-2 w-full rounded border p-2 text-xs" value={result?.run_id || ""} onChange={e => restore(e.target.value)}>
          <option value="">选择运行</option>{runs.map(r => <option key={r.run_id} value={r.run_id}>{r.created_at} · {r.status} · {r.derived_fact_count} 条</option>)}
        </select></label>
        {result && <>
          <div className="flex flex-wrap items-center gap-3 text-sm">
            <strong>派生事实 {result.inferred_facts.length}</strong><span>命中规则 {result.rules_fired.length}</span>
            <span data-testid="run-status">{result.status === "applied" ? "已保存并投影" : "预览（未写图）"}</span>
            <button className={button} disabled={busy || result.status === "applied"} onClick={save}>保存结果</button>
            <button className={button} onClick={jumpToGraph}>查看预览图</button>
          </div>
          <p className="rounded bg-violet-50 p-2 text-xs text-violet-700">{result.status === "preview" ? "关系图预览已生成：紫色虚线表示本次推理的临时派生关系，尚未写入图数据库。" : "关系图已加载：紫色实线表示已保存的派生关系。"}</p>
          {result.graph.reason && <p className="text-sm text-amber-700">{result.graph.reason} {result.graph.missing_nodes?.join(", ")}</p>}
          <input aria-label="搜索派生事实" placeholder="搜索派生事实，例如 MEASURES_EQUIPMENT" value={query} onChange={e => setQuery(e.target.value)} className="w-full rounded border p-2 text-sm" />
          <div className="max-h-80 space-y-2 overflow-auto">{visible.map(item => <button key={item.conclusion} className="block w-full rounded border p-2 text-left font-mono text-xs hover:bg-violet-50" onClick={() => focus(item)}>{item.conclusion}<span className="mt-1 block font-sans text-violet-700">查看证据 · {item.evidence.length} 条来源记录</span></button>)}</div>
        </>}
      </section>
    </div>
    {selected && <section data-testid="proof-panel" className="space-y-3 rounded-xl border border-violet-200 bg-white p-5">
      <h3 className="font-semibold">推理证据</h3><p className="break-all font-mono text-sm">{selected.conclusion}</p>
      <p className="break-all text-sm">命中规则：{selected.rule}</p>
      <div className="text-sm">直接前提：{selected.premises.map(p => <button key={p} onClick={() => {const parent = result?.inferred_facts.find(f => f.conclusion === p); if(parent) setSelected(parent);}} className="my-1 block break-all text-left font-mono text-xs text-violet-700">{p}{result?.inferred_facts.some(f => f.conclusion === p) ? " （点击追溯上一步）" : " （输入事实）"}</button>)}</div>
      {selected.evidence.map((e, i) => <details key={i} className="rounded bg-slate-50 p-3 text-xs" open={i === 0}>
        <summary className="cursor-pointer break-all">{e.source_file ? `${e.source_file} · 行/定位 ${e.source_row}` : e.source === "manual_input" ? "手工输入（无文件来源）" : "图事实（缺少原始证据引用）"}</summary>
        <p className="my-2 break-all">前提：{e.fact}</p><p className="break-all">EvidenceRef：{e.evidence_ref_id || "无"}</p>
        <pre className="mt-2 whitespace-pre-wrap break-all">{e.evidence_text || "没有原始文件证据"}</pre>
        <p className="mt-2 break-all">内容 SHA-256：{e.content_hash || "无"}<br/>来源版本：{e.source_version || "无"}</p>
      </details>)}
    </section>}
    <section className="rounded-xl border bg-white p-5" data-testid="instance-graph">
      <div className="flex flex-wrap items-center gap-4"><h3 className="font-semibold">实例与派生关系图</h3>
        <span data-testid="graph-counts" className="text-sm">{graph?.nodes.length || 0} 个实例 · 已保存 {graph?.edges.filter(e => e.properties?.derived && e.properties.reasoning_run_id === result?.run_id).length || 0} 条 · 预览 {result?.status === "preview" ? result.inferred_facts.filter(f => /^\w+\([^,]+,[^\)]+\)$/.test(f.conclusion)).length : 0} 条</span>
        <label className="text-sm"><input type="checkbox" checked={derivedOnly} onChange={e => setDerivedOnly(e.target.checked)} /> 仅派生关系</label>
        <button className={button} onClick={() => cy.current?.fit(undefined, 30)}>显示全图</button>
        <button className={button} onClick={() => refresh().catch(e => setError(message(e)))}>刷新实例图</button>
      </div>
      <p className="my-2 text-xs text-slate-500">灰色为来源关系；紫色虚线为本次运行的临时预览，紫色实线为已保存的派生关系。点击关系查看证据；滚轮缩放、拖拽平移。</p>
      {graph && !graph.available && <p className="text-red-700">图数据库不可用</p>}
      <div ref={canvas} className="h-[460px] w-full" />
      {nodeDetails && <details className="mt-3 rounded bg-slate-50 p-3 text-xs" open><summary>选中对象 / 关系</summary><pre className="whitespace-pre-wrap break-all">{JSON.stringify(nodeDetails, null, 2)}</pre></details>}
    </section>
  </div>;
}
