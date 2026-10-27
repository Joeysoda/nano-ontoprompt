import { useState } from 'react'
import StudyGraphCanvas from './StudyGraphCanvas'
import RowMappingEditor from './RowMappingEditor'
import { nodeKinds } from './studyNodeKinds'
import { removeStudyEdges } from './studyGraphEditing'

type Row = Record<string, unknown>
export type StudyLogicAsset = { asset_key: string; name: string; version: string; status: string; side_effect: boolean; input_schema: Row; output_schema: Row }

export type EditableStudySpec = {
  parameters: Row[]
  nodes: Row[]
  edges: Row[]
  metrics: Row[]
  constraints: Row[]
  [key: string]: unknown
}

const text = (value: unknown) => value == null ? '' : String(value)
const parseJson = (value: string) => JSON.parse(value) as unknown
const row = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}

export default function StudySpecBuilder({ spec, onChange, scope = 'draft', assets = [] }: { spec: EditableStudySpec; onChange: (spec: EditableStudySpec) => void; scope?: string; assets?: StudyLogicAsset[] }) {
  const [selectedKey, setSelectedKey] = useState('')
  const [newKind, setNewKind] = useState('function')
  const [validationError, setValidationError] = useState('')
  const selectedIndex = Math.max(0, spec.nodes.findIndex(node => node.key === selectedKey))
  const changeRow = (section: keyof EditableStudySpec, index: number, patch: Row) => {
    const rows = [...(spec[section] as Row[])]
    rows[index] = { ...rows[index], ...patch }
    if (section === 'nodes' && patch.key && patch.key !== spec.nodes[index].key) {
      const oldKey = text(spec.nodes[index].key), newKey = text(patch.key)
      if (!/^[A-Za-z][A-Za-z0-9_-]*$/.test(newKey) || spec.nodes.some((node, nodeIndex) => nodeIndex !== index && node.key === newKey)) { setValidationError('节点键必须唯一，以字母开头，只包含字母、数字、下划线或短横线。'); return }
      setValidationError('')
      const rename = (value: unknown) => typeof value === 'string' && value.startsWith(`${oldKey}.`) ? `${newKey}${value.slice(oldKey.length)}` : value
      setSelectedKey(newKey)
      onChange({ ...spec, nodes: rows.map(node => ({ ...node, inputs: Object.fromEntries(Object.entries(row(node.inputs)).map(([port, binding]) => [port, row(binding).from ? { ...row(binding), from: rename(row(binding).from) } : binding])) })), edges: spec.edges.map(edge => ({ ...edge, source: rename(edge.source), target: rename(edge.target) })), metrics: spec.metrics.map(metric => ({ ...metric, source: rename(metric.source) })), constraints: spec.constraints.map(item => ({ ...item, expression_ref: rename(item.expression_ref) })) })
    } else onChange({ ...spec, [section]: rows })
  }
  const addRow = (section: keyof EditableStudySpec, row: Row) => onChange({ ...spec, [section]: [...(spec[section] as Row[]), row] })
  const removeRow = (section: keyof EditableStudySpec, index: number) => {
    if (section === 'edges') { onChange(removeStudyEdges(spec, [index])); return }
    const updated = { ...spec, [section]: (spec[section] as Row[]).filter((_, rowIndex) => rowIndex !== index) }
    if (section === 'nodes') {
      const prefix = `${spec.nodes[index].key}.`
      updated.edges = spec.edges.filter(edge => !text(edge.source).startsWith(prefix) && !text(edge.target).startsWith(prefix))
      updated.metrics = spec.metrics.filter(metric => !text(metric.source).startsWith(prefix))
      updated.constraints = spec.constraints.filter(item => !text(item.expression_ref).startsWith(prefix))
      updated.nodes = updated.nodes.map(node => ({ ...node, inputs: Object.fromEntries(Object.entries(row(node.inputs)).map(([port, binding]) => [port, text(row(binding).from).startsWith(prefix) ? null : binding])) }))
    }
    onChange(updated)
  }
  const outputOptions = spec.nodes.flatMap(node => Object.keys(row(node.outputs)).map(port => `${node.key}.${port}`))

  return <section className="mt-5 flex flex-col gap-5" aria-label="Study 定义表单">
    {validationError && <p role="alert" className="text-sm text-rose-700">{validationError}</p>}
    <details className="border border-slate-200 p-4"><summary className="cursor-pointer text-sm font-medium">输入参数 · {spec.parameters.length}</summary><div className="mt-4">
      <div className="flex items-center justify-between"><div><h3 className="font-semibold">输入参数</h3><p className="mt-1 text-xs text-slate-500">业务用户运行 Case 时可以调整这些值。结构化 JSON 适合供应链和生产数据。</p></div><button className="wb-button-secondary" onClick={() => addRow('parameters', { key: `parameter_${spec.parameters.length + 1}`, label: '新参数', type: 'decimal', default: 0 })}>＋ 添加参数</button></div>
      <div className="mt-3 space-y-2">{spec.parameters.map((parameter, index) => <div className="grid gap-2 rounded border bg-slate-50 p-3 md:grid-cols-6" key={`${index}:${text(parameter.key)}`}>
        <label className="text-xs">参数键<input className="wb-input mt-1 w-full" value={text(parameter.key)} onChange={event => changeRow('parameters', index, { key: event.target.value })} /></label>
        <label className="text-xs">显示名称<input className="wb-input mt-1 w-full" value={text(parameter.label)} onChange={event => changeRow('parameters', index, { label: event.target.value })} /></label>
        <label className="text-xs">类型<select className="wb-input mt-1 w-full" value={text(parameter.type || 'string')} onChange={event => changeRow('parameters', index, { type: event.target.value })}>{['string', 'integer', 'decimal', 'boolean', 'json', 'datetime', 'object_ref', 'object_set'].map(kind => <option key={kind}>{kind}</option>)}</select></label>
        <label className="text-xs">单位<input className="wb-input mt-1 w-full" value={text(parameter.unit)} onChange={event => changeRow('parameters', index, { unit: event.target.value || null })} placeholder="min、件、kWh" /></label>
        <label className="text-xs">默认值<input className="wb-input mt-1 w-full" value={typeof parameter.default === 'object' ? JSON.stringify(parameter.default) : text(parameter.default)} onChange={event => changeRow('parameters', index, { default: event.target.value })} onBlur={event => { if (['integer', 'decimal', 'json', 'boolean'].includes(text(parameter.type))) { try { changeRow('parameters', index, { default: parseJson(event.target.value) }) } catch { /* Keep the draft visible; validation explains malformed values. */ } } }} /></label>
        <div className="flex items-end justify-between"><label className="mb-2 flex items-center gap-1 text-xs"><input type="checkbox" checked={Boolean(parameter.required)} onChange={event => changeRow('parameters', index, { required: event.target.checked })} />必填</label><button className="mb-2 text-xs text-rose-700" onClick={() => removeRow('parameters', index)}>删除</button></div>
      </div>)}</div>
    </div></details>

    <div className="order-first border border-slate-200 bg-white">
      <div className="flex flex-wrap items-center justify-between gap-3 border-b px-4 py-3"><div><h3 className="font-semibold">计算图</h3><p className="mt-1 text-xs text-slate-500">选择模块查看配置，连接端口传递计算结果。</p></div><div className="flex gap-2"><select aria-label="添加节点类型" className="wb-input" value={newKind} onChange={event => setNewKind(event.target.value)}>{Object.entries(nodeKinds).map(([kind, info]) => <option key={kind} value={kind}>{info.label}</option>)}</select><button className="wb-button-secondary" onClick={() => {
        let number = spec.nodes.length + 1
        while (spec.nodes.some(node => node.key === `step_${number}`)) number++
        const key = `step_${number}`
        const node: Row = { key, kind: newKind, inputs: newKind === 'compare' ? { left: 0, right: 0 } : { value: 0 }, outputs: { value: 'number' }, config: newKind === 'aggregate_metric' ? { operation: 'sum' } : newKind === 'compare' ? { operation: 'delta' } : {} }
        if (newKind === 'row_mapping') { node.inputs = { rows: [] }; node.outputs = { rows: 'rows', lineage: 'lineage' }; node.config = { fields: [] } }
        if (nodeKinds[newKind].logic) node.implementation = { key: '', version: '' }
        addRow('nodes', node); setSelectedKey(key)
      }}>＋ 添加计算节点</button></div></div>
      <div className="grid min-w-0 lg:grid-cols-[minmax(0,1fr)_280px]"><StudyGraphCanvas key={scope} scope={scope} spec={spec} onChange={onChange} selectedKey={text(spec.nodes[selectedIndex]?.key)} onSelect={setSelectedKey} /><aside className="max-h-[440px] overflow-auto border-l p-3"><h4 className="mb-3 text-xs font-semibold uppercase tracking-wider text-slate-500">节点配置</h4>{spec.nodes.filter((_, index) => index === selectedIndex).map(node => { const index = selectedIndex; return <article className="rounded border bg-slate-50 p-3" key={`${index}:${text(node.key)}`}><div className="grid gap-2 sm:grid-cols-1">
        <label className="text-xs">节点键<input className="wb-input mt-1 w-full" defaultValue={text(node.key)} onBlur={event => changeRow('nodes', index, { key: event.target.value })} /></label>
        <label className="text-xs">节点类型<select aria-label="节点类型" className="wb-input mt-1 w-full" value={text(node.kind)} onChange={event => changeRow('nodes', index, { kind: event.target.value, implementation: nodeKinds[event.target.value]?.logic ? node.implementation ?? { key: '', version: '' } : null })}>{Object.entries(nodeKinds).map(([kind, info]) => <option key={kind} value={kind}>{info.label}</option>)}</select></label>
        <div className="flex items-end justify-between"><span className="mb-2 text-xs text-slate-500">{Object.keys(row(node.outputs)).map(port => `${node.key}.${port}`).join(', ') || '尚无输出'}</span><button className="mb-2 text-xs text-rose-700" onClick={() => removeRow('nodes', index)}>删除</button></div>
      </div>
        {node.kind === 'row_mapping' && <RowMappingEditor config={row(node.config)} onChange={config => changeRow('nodes', index, { config })} />}
        {nodeKinds[text(node.kind)]?.logic && <div className="mt-2 grid gap-2 sm:grid-cols-2"><label className="text-xs">{node.kind === 'action_preview' ? 'Action ID' : 'Logic Asset'}<input className="wb-input mt-1 w-full" value={text(row(node.implementation).key)} onChange={event => changeRow('nodes', index, { implementation: { ...row(node.implementation), key: event.target.value } })} /></label><label className="text-xs">固定版本<input className="wb-input mt-1 w-full" value={text(row(node.implementation).version)} onChange={event => changeRow('nodes', index, { implementation: { ...row(node.implementation), version: event.target.value } })} /></label></div>}
        {['function', 'time_series'].includes(text(node.kind)) && <label className="mt-3 block text-xs">选择已发布逻辑<select aria-label="选择已发布逻辑" className="wb-input mt-1 w-full" value="" onChange={event => {
          const asset = assets.find(asset => `${asset.asset_key}@${asset.version}` === event.target.value)
          if (!asset) return
          const inputs = Object.fromEntries(Object.keys(row(asset.input_schema.properties)).map(key => [key, Object.hasOwn(row(node.inputs), key) ? row(node.inputs)[key] : spec.parameters.some(parameter => parameter.key === key) ? { parameter: key } : { value: row(row(asset.input_schema.properties)[key]).default ?? null }]))
          const outputs = Object.fromEntries(Object.keys(row(asset.output_schema.properties)).map(key => [key, key]))
          const prefix = `${node.key}.`
          const obsolete = spec.edges.flatMap((edge, edgeIndex) => (text(edge.target).startsWith(prefix) && !Object.hasOwn(inputs, text(edge.target).slice(prefix.length))) || (text(edge.source).startsWith(prefix) && !Object.hasOwn(outputs, text(edge.source).slice(prefix.length))) ? [edgeIndex] : [])
          const cleaned = removeStudyEdges(spec, obsolete)
          onChange({ ...cleaned, nodes: cleaned.nodes.map((item, itemIndex) => itemIndex === index ? { ...item, implementation: { key: asset.asset_key, version: asset.version }, inputs, outputs } : item), metrics: spec.metrics.filter(metric => !text(metric.source).startsWith(prefix) || Object.hasOwn(outputs, text(metric.source).slice(prefix.length))), constraints: spec.constraints.filter(item => !text(item.expression_ref).startsWith(prefix) || Object.hasOwn(outputs, text(item.expression_ref).slice(prefix.length))) })
        }}><option value="">选择逻辑与版本…</option>{assets.filter(asset => asset.status === 'published' && !asset.side_effect).map(asset => <option key={`${asset.asset_key}@${asset.version}`} value={`${asset.asset_key}@${asset.version}`}>{asset.name} · {asset.version}</option>)}</select><span className="mt-1 block text-slate-500">选中后生成输入与输出端口，并匹配同名研究参数。</span></label>}
        {['aggregate_metric', 'compare'].includes(text(node.kind)) && <label className="mt-3 block text-xs">计算方式<select className="wb-input mt-1 w-full" value={text(row(node.config).operation || (node.kind === 'compare' ? 'delta' : 'count'))} onChange={event => changeRow('nodes', index, { config: { ...row(node.config), operation: event.target.value } })}>{(node.kind === 'compare' ? [['delta', '差值（右 − 左）'], ['ratio', '比值（右 ÷ 左）'], ['equal', '是否相等']] : [['count', '计数'], ['sum', '求和'], ['mean', '平均值'], ['min', '最小值'], ['max', '最大值']]).map(([value, label]) => <option key={value} value={value}>{label}</option>)}</select></label>}
        <p className="mt-3 text-xs leading-5 text-slate-500">{nodeKinds[text(node.kind)]?.description}</p><details className="mt-3 text-xs"><summary>节点输入、输出和配置</summary><div className="mt-2 grid gap-2 md:grid-cols-1">{(['inputs', 'outputs', 'config'] as const).map(field => <label className="text-xs" key={`${field}:${JSON.stringify(node[field])}`}>{field} JSON<textarea className="wb-input mt-1 min-h-24 w-full font-mono" defaultValue={JSON.stringify(node[field] ?? {}, null, 2)} onBlur={event => { try { changeRow('nodes', index, { [field]: parseJson(event.target.value) }) } catch { /* Keep the invalid draft in this editor until corrected. */ } }} /></label>)}</div></details>
      </article> })}</aside></div>
      <details className="border-t px-4 py-3 text-xs"><summary>依赖列表 · {spec.edges.length} 条连线</summary>{spec.edges.map((edge, index) => <div key={index} className="mt-2 flex items-center justify-between gap-2"><span>{text(edge.source)} → {text(edge.target)}</span><button className="text-rose-700" onClick={() => removeRow('edges', index)}>删除连线</button></div>)}</details>
    </div>

    <details className="border border-slate-200 p-4"><summary className="cursor-pointer text-sm font-medium">输出指标与约束 · {spec.metrics.length} 个指标 / {spec.constraints.length} 条约束</summary><div className="mt-4 grid gap-5 lg:grid-cols-2">
      <div className="rounded-lg border border-slate-200 p-4"><div className="flex items-center justify-between"><div><h3 className="font-semibold">输出指标</h3><p className="mt-1 text-xs text-slate-500">指标必须来自某个计算节点的已声明输出端口。</p></div><button className="wb-button-secondary" disabled={!outputOptions.length} onClick={() => addRow('metrics', { key: `metric_${spec.metrics.length + 1}`, label: '新指标', type: 'decimal', direction: 'neutral', unit: '', source: outputOptions[0] })}>＋ 添加指标</button></div><div className="mt-3 space-y-2">{spec.metrics.map((metric, index) => <div className="grid gap-2 rounded border bg-slate-50 p-3 sm:grid-cols-2" key={`${index}:${text(metric.key)}`}><label className="text-xs">键<input className="wb-input mt-1 w-full" value={text(metric.key)} onChange={event => changeRow('metrics', index, { key: event.target.value })} /></label><label className="text-xs">名称<input className="wb-input mt-1 w-full" value={text(metric.label)} onChange={event => changeRow('metrics', index, { label: event.target.value })} /></label><label className="text-xs">来源<select className="wb-input mt-1 w-full" value={text(metric.source)} onChange={event => changeRow('metrics', index, { source: event.target.value })}>{outputOptions.map(value => <option key={value}>{value}</option>)}</select></label><label className="text-xs">类型 / 目标<select className="wb-input mt-1 w-full" value={`${text(metric.type)}:${text(metric.direction)}`} onChange={event => { const [type, direction] = event.target.value.split(':'); changeRow('metrics', index, { type, direction }) }}>{['integer', 'decimal', 'boolean', 'duration'].flatMap(type => ['minimize', 'maximize', 'neutral'].map(direction => <option key={`${type}:${direction}`} value={`${type}:${direction}`}>{type} · {direction}</option>))}</select></label><div className="flex items-center justify-between sm:col-span-2"><input aria-label="指标单位" className="wb-input max-w-48" value={text(metric.unit)} onChange={event => changeRow('metrics', index, { unit: event.target.value })} placeholder="单位" /><button className="text-xs text-rose-700" onClick={() => removeRow('metrics', index)}>删除指标</button></div></div>)}</div></div>
      <div className="rounded-lg border border-slate-200 p-4"><div className="flex items-center justify-between"><div><h3 className="font-semibold">约束</h3><p className="mt-1 text-xs text-slate-500">运行会记录每条约束是否满足和观测值。</p></div><button className="wb-button-secondary" disabled={!outputOptions.length} onClick={() => addRow('constraints', { key: `constraint_${spec.constraints.length + 1}`, expression_ref: outputOptions[0], severity: 'hard', config: { operator: 'lte', value: 0 } })}>＋ 添加约束</button></div><div className="mt-3 space-y-2">{spec.constraints.map((constraint, index) => <div className="grid gap-2 rounded border bg-slate-50 p-3 sm:grid-cols-2" key={`${index}:${text(constraint.key)}`}><label className="text-xs">键<input className="wb-input mt-1 w-full" value={text(constraint.key)} onChange={event => changeRow('constraints', index, { key: event.target.value })} /></label><label className="text-xs">来源<select className="wb-input mt-1 w-full" value={text(constraint.expression_ref)} onChange={event => changeRow('constraints', index, { expression_ref: event.target.value })}>{outputOptions.map(value => <option key={value}>{value}</option>)}</select></label><label className="text-xs">严重级别<select className="wb-input mt-1 w-full" value={text(constraint.severity)} onChange={event => changeRow('constraints', index, { severity: event.target.value })}><option value="hard">hard</option><option value="soft">soft</option></select></label><label className="text-xs">比较规则 JSON<input className="wb-input mt-1 w-full font-mono" defaultValue={JSON.stringify(constraint.config ?? {})} onBlur={event => { try { changeRow('constraints', index, { config: parseJson(event.target.value) }) } catch { /* Keep the invalid draft visible for validation. */ } }} /></label><button className="text-left text-xs text-rose-700 sm:col-span-2" onClick={() => removeRow('constraints', index)}>删除约束</button></div>)}</div></div>
    </div></details>
  </section>
}
