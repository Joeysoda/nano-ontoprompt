import { useState, type DragEvent, type MouseEvent } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClientV2, formatApiError } from '@/api/client'
import type { Row } from '@/components/dashboard/binding'
import type { Runtime } from './WidgetView'
import { applyOverrides, bindSchedule, outputResources, outputTime, packScheduleLanes, parseScheduleSearchOutput, parseScheduleValidationOutput, snapTime, validateSchedule, type ScheduleBusinessValidation, type ScheduleCreate, type ScheduleOverride, type ScheduleSearchResult, type ScheduledTask } from './scheduleModel'

type P = Record<string, unknown>
type Props = { rows: Row[]; p: P; rt: Runtime; title: string }
const text = (value: unknown) => value === undefined || value === null ? '' : String(value)
const number = (value: unknown, fallback: number) => Number.isFinite(Number(value)) ? Number(value) : fallback
const fmt = (value: number) => new Date(value).toISOString().slice(0, 16).replace('T', ' ')

export default function ScheduleGanttWidget({ rows, p, rt, title }: Props) {
  const signature = JSON.stringify(rows)
  const [proposal, setProposal] = useState<{ signature: string; values: Record<string, ScheduleOverride> }>({ signature: '', values: {} })
  const overrides = proposal.signature === signature ? proposal.values : {}
  const setOverrides = (change: (current: Record<string, ScheduleOverride>) => Record<string, ScheduleOverride>) =>
    setProposal(current => ({ signature, values: change(current.signature === signature ? current.values : {}) }))
  const [previewState, setPreviewState] = useState<{ signature: string; value: { edit_count: number } | null }>({ signature: '', value: null })
  const preview = previewState.signature === signature ? previewState.value : null
  const setPreview = (value: { edit_count: number } | null) => setPreviewState({ signature, value })
  const [message, setMessage] = useState('')
  const [selectedTask, setSelectedTask] = useState('')
  const [searchState, setSearchState] = useState<{ signature: string; value: ScheduleSearchResult | null }>({ signature: '', value: null })
  const [validationState, setValidationState] = useState<{ signature: string; value: ScheduleBusinessValidation | null }>({ signature: '', value: null })
  const [createProposal, setCreateProposal] = useState<{ signature: string; values: ScheduleCreate[] }>({ signature: '', values: [] })
  const creates = createProposal.signature === signature ? createProposal.values : []
  const setCreates = (change: (current: ScheduleCreate[]) => ScheduleCreate[]) => setCreateProposal(current => ({ signature, values: change(current.signature === signature ? current.values : []) }))
  const [createDrag, setCreateDrag] = useState<{ resource: string; x: number } | null>(null)
  const [showDependencies, setShowDependencies] = useState(() => !p.hide_dependency_arrows)
  const [fallbackStart] = useState(() => Date.now())
  let boundError = '', base = [] as ReturnType<typeof bindSchedule>
  try { base = bindSchedule(rows, p) } catch (error) { boundError = error instanceof Error ? error.message : '排程数据无效' }
  const createdTasks: ScheduledTask[] = creates.map(task => ({ ...task, objectType: '', objectId: task.id, predecessors: [], raw: {}, sourceStart: null, sourceEnd: null, sourceResource: task.resources }))
  const tasks = [...applyOverrides(base, overrides), ...createdTasks]
  const scheduled = tasks.filter(row => row.resources.length > 0 && row.start !== null && row.end !== null)
  const unscheduled = tasks.filter(row => !row.resources.length || row.start === null || row.end === null)
  const configuredResources = Array.isArray(p.resources) ? p.resources as Array<{ id?: unknown; label?: unknown }> : []
  const resourceLabels = new Map(configuredResources.map(row => [text(row.id), text(row.label) || text(row.id)]))
  for (const task of scheduled) for (const resource of task.resources) if (!resourceLabels.has(resource)) resourceLabels.set(resource, resource)
  const resources = [...resourceLabels].slice(0, 200)
  const starts = scheduled.map(row => row.start!), ends = scheduled.map(row => row.end!)
  const minimum = starts.length ? Math.min(...starts) : fallbackStart
  const maximum = ends.length ? Math.max(...ends) : minimum + 8 * 3_600_000
  const padding = Math.max(3_600_000, (maximum - minimum) * 0.05)
  const rangeStart = minimum - padding, rangeEnd = Math.max(maximum + padding, rangeStart + 3_600_000), span = rangeEnd - rangeStart
  const violations = validateSchedule(tasks, p)
  const blocked = !!p.enforce_rules && violations.length > 0
  const changed = Object.entries(overrides)
  const proposalCount = changed.length + creates.length
  const searchSignature = JSON.stringify({ signature, overrides, creates })
  const searchResult = searchState.signature === searchSignature ? searchState.value : null
  const businessValidation = validationState.signature === searchSignature ? validationState.value : null
  const scenarioId = rt.vars[text(p.scenario_variable)] ?? ''
  const targetsReady = !text(p.target_parameter) || changed.every(([id]) => { const task = base.find(row => row.id === id); return !!task?.objectType && !!task.objectId })
  const modifyReady = !changed.length || (!!text(p.action_type_id) && !!text(p.start_parameter) && !!text(p.end_parameter) && !!text(p.resource_parameter) && targetsReady)
  const createReady = !creates.length || (!!text(p.create_action_type_id) && !!text(p.create_start_parameter) && !!text(p.create_end_parameter) && !!text(p.create_resource_parameter))
  const canPersist = !!scenarioId && modifyReady && createReady

  const actions = () => changed.map(([id, change]) => {
    const task = base.find(row => row.id === id)!
    const parameters: Record<string, unknown> = {
      [text(p.start_parameter)]: outputTime(task.sourceStart, change.start),
      [text(p.end_parameter)]: outputTime(task.sourceEnd, change.end),
      [text(p.resource_parameter)]: outputResources(task.sourceResource, change.resources),
    }
    if (text(p.target_parameter)) parameters[text(p.target_parameter)] = { object_type: task.objectType, object_id: task.objectId }
    return { action_type_id: text(p.action_type_id), parameters }
  }).concat(creates.map(task => { const parameters: Record<string, unknown> = {
    [text(p.create_start_parameter)]: new Date(task.start).toISOString(), [text(p.create_end_parameter)]: new Date(task.end).toISOString(),
    [text(p.create_resource_parameter)]: text(p.resource_cardinality) === 'many' ? task.resources : task.resource,
  }; if (text(p.create_id_parameter)) parameters[text(p.create_id_parameter)] = task.id; if (text(p.create_label_parameter)) parameters[text(p.create_label_parameter)] = task.label
    return { action_type_id: text(p.create_action_type_id), parameters }
  }))
  const payload = async () => {
    if (proposalCount > 100) throw new Error('一次最多提交 100 个排程修改')
    const scenario = await apiClientV2.get<{ etag: number }>(`/ontologies/${rt.ontologyId}/scenarios/${scenarioId}`)
    return { expected_etag: scenario.etag, client_request_id: crypto.randomUUID(), actions: actions() }
  }
  const review = useMutation({ mutationFn: async () => apiClientV2.post<{ edit_count: number }>(`/ontologies/${rt.ontologyId}/scenarios/${scenarioId}/actions:preview`, await payload()), onSuccess: result => { setPreview(result); setMessage('') } })
  const save = useMutation({ mutationFn: async () => apiClientV2.put(`/ontologies/${rt.ontologyId}/scenarios/${scenarioId}/actions`, await payload()), onSuccess: async () => { setOverrides(() => ({})); setCreates(() => []); setPreview(null); setMessage('已保存为 Scenario 修订'); await rt.refresh() } })
  const functionInputs = (schema: 'schedule-search/v1' | 'schedule-validation/v1') => ({
    schema, domain_start: new Date(rangeStart).toISOString(), domain_end: new Date(rangeEnd).toISOString(), scenario_id: scenarioId || null,
    schedule_objects: tasks.map(task => ({ id: task.id, label: task.label, container_id: task.resource || null, container_ids: task.resources, layer_id: task.layer, start: task.start === null ? null : new Date(task.start).toISOString(), end: task.end === null ? null : new Date(task.end).toISOString(), predecessor_ids: task.predecessors, object_type: task.objectType || null, object_id: task.objectId, attributes: task.raw })),
  })
  const search = useMutation({ mutationFn: async () => {
    const assetId = text(p.recommendation_asset_id)
    if (!assetId || !selectedTask) throw new Error('请先配置推荐 Logic Asset 并选择任务')
    const response = await apiClientV2.post<{ output: unknown }>(`/ontologies/${rt.ontologyId}/logic-assets/${assetId}/run`, { inputs: {
      ...functionInputs('schedule-search/v1'),
      trigger: { kind: 'PUCK', schedulable_object_primary_key: selectedTask },
    } })
    return parseScheduleSearchOutput(response.output)
  }, onSuccess: result => setSearchState({ signature: searchSignature, value: result }) })
  const businessCheck = useMutation({ mutationFn: async () => {
    const assetId = text(p.validation_asset_id)
    if (!assetId) throw new Error('请先配置业务校验 Logic Asset')
    const response = await apiClientV2.post<{ output: unknown }>(`/ontologies/${rt.ontologyId}/logic-assets/${assetId}/run`, { inputs: functionInputs('schedule-validation/v1') })
    return parseScheduleValidationOutput(response.output)
  }, onSuccess: result => setValidationState({ signature: searchSignature, value: result }) })

  const propose = (id: string, patch: Partial<ScheduleOverride>) => {
    const task = tasks.find(row => row.id === id)
    if (!task) return
    const start = patch.start ?? task.start ?? snapTime(rangeStart, number(p.snap_minutes, 15))
    const duration = task.start !== null && task.end !== null ? task.end - task.start : number(p.default_duration_minutes, 60) * 60_000
    const end = patch.end ?? start + duration
    const resources = patch.resources ?? (patch.resource ? [patch.resource] : task.resources), resource = resources[0] ?? ''
    if (!resource || end <= start) return
    setOverrides(current => ({ ...current, [id]: { resource, resources, start, end } })); setPreview(null); setSearchState({ signature: '', value: null }); setValidationState({ signature: '', value: null }); setMessage('')
  }

  const drop = (event: DragEvent<HTMLDivElement>, resource: string) => {
    event.preventDefault()
    const id = event.dataTransfer.getData('text/schedule-id'), task = tasks.find(row => row.id === id)
    if (!task) return
    const rect = event.currentTarget.getBoundingClientRect(), fraction = Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width))
    const duration = task.start !== null && task.end !== null ? task.end - task.start : number(p.default_duration_minutes, 60) * 60_000
    const behavior = text(p.allocation_behavior || 'allocate')
    const start = behavior === 'resource_only' && task.start !== null ? task.start : snapTime(rangeStart + fraction * span, number(p.snap_minutes, 15))
    const targetResources = behavior === 'time_only' && task.resources.length ? task.resources : text(p.resource_cardinality) === 'many' && text(p.resource_drop_behavior) === 'append' ? [...new Set([...task.resources, resource])] : [resource]
    propose(id, { resource: targetResources[0], resources: targetResources, start, end: start + duration })
  }
  const createStart = (event: MouseEvent<HTMLDivElement>, resource: string) => { if (event.shiftKey && text(p.create_action_type_id)) { event.preventDefault(); setCreateDrag({ resource, x: event.clientX }) } }
  const createEnd = (event: MouseEvent<HTMLDivElement>, resource: string) => {
    if (!createDrag || createDrag.resource !== resource) return
    const rect = event.currentTarget.getBoundingClientRect(), a = snapTime(rangeStart + Math.max(0, Math.min(1, (createDrag.x - rect.left) / rect.width)) * span, number(p.snap_minutes, 15)), b = snapTime(rangeStart + Math.max(0, Math.min(1, (event.clientX - rect.left) / rect.width)) * span, number(p.snap_minutes, 15))
    const step = number(p.snap_minutes, 15) * 60_000, start = Math.min(a, b), end = Math.max(a, b, start + step), id = `draft-${crypto.randomUUID()}`
    setCreates(current => [...current, { id, label: text(p.create_default_label) || '新任务', resource, resources: [resource], layer: text(p.create_layer) || 'default', start, end }]); setCreateDrag(null); setPreview(null); setSearchState({ signature: '', value: null }); setValidationState({ signature: '', value: null }); setMessage('')
  }
  const startDrag = (event: DragEvent, id: string) => { event.dataTransfer.setData('text/schedule-id', id); event.dataTransfer.effectAllowed = 'move' }
  const validationErrors = businessValidation?.state === 'result' ? businessValidation.violations.filter(issue => issue.severity === 'error') : []
  const businessBlocked = !!text(p.validation_asset_id) && p.enforce_business_rules !== false && (!businessValidation || businessValidation.state === 'error' || validationErrors.length > 0)
  const configuredLayers = Array.isArray(p.layers) ? p.layers as Array<{ id?: unknown; label?: unknown; color?: unknown; style?: unknown }> : []
  const layerMap = new Map(configuredLayers.map(layer => [text(layer.id), { id: text(layer.id), label: text(layer.label) || text(layer.id), color: text(layer.color) || '#2563eb', style: text(layer.style) || 'standard' }]))
  for (const task of tasks) if (!layerMap.has(task.layer)) layerMap.set(task.layer, { id: task.layer, label: task.layer === 'default' ? '默认层' : task.layer, color: '#2563eb', style: 'standard' })
  const rowModels = resources.map(([resource, label]) => { const resourceTasks = scheduled.filter(task => task.resources.includes(resource)), layout = packScheduleLanes(resourceTasks); return { resource, label, tasks: resourceTasks, layout, height: layout.laneCount * 44 + 8, top: 0 } })
  let accumulatedTop = 0
  for (const row of rowModels) { row.top = accumulatedTop; accumulatedTop += row.height }
  const taskPoints = new Map<string, { startX: number; endX: number; y: number }>()
  for (const row of rowModels) for (const task of row.tasks) if (!taskPoints.has(task.id)) taskPoints.set(task.id, { startX: (task.start! - rangeStart) / span * 1000, endX: (task.end! - rangeStart) / span * 1000, y: row.top + 8 + (row.layout.lanes.get(task.id) ?? 0) * 44 + 18 })
  const dependencyArrows = tasks.flatMap(task => task.predecessors.map(predecessor => ({ from: base.find(row => row.id === predecessor), to: task })))
    .filter(edge => edge.from && edge.from.layer === edge.to.layer && layerMap.get(edge.to.layer)?.style === 'standard' && taskPoints.has(edge.from.id) && taskPoints.has(edge.to.id)) as Array<{ from: ScheduledTask; to: ScheduledTask }>
  if (boundError) return <p role="alert" className="text-sm text-rose-700">{boundError}</p>
  if (!tasks.length && !resources.length) return <p className="text-sm text-slate-500">没有排程任务或可创建任务的资源</p>
  return <section className="space-y-2" aria-label={title || '交互式排程'} data-testid="schedule-gantt">
    <div className="flex flex-wrap items-center gap-2 text-xs"><strong>{title || '交互式排程'}</strong><span>{scheduled.length} 已排 · {unscheduled.length} 未排 · {proposalCount} 待保存</span>{dependencyArrows.length > 0 && <button className="text-blue-700" onClick={() => setShowDependencies(value => !value)}>{showDependencies ? '隐藏依赖箭头' : '显示依赖箭头'}</button>}<span className="ml-auto text-slate-500">UTC · {number(p.snap_minutes, 15)} 分钟吸附</span></div>
    {!rt.editing && text(p.create_action_type_id) && <p className="text-[11px] text-slate-500">按住 Shift 在资源行空白处拖拽，可创建本地任务提案。</p>}
    {layerMap.size > 1 && <div className="flex flex-wrap gap-2 text-[11px]" aria-label="排程层图例">{[...layerMap.values()].map(layer => <span key={layer.id} className="inline-flex items-center gap-1"><i className="h-2.5 w-2.5 rounded-sm" style={{ backgroundColor: layer.color }} />{layer.label} · {layer.style}</span>)}</div>}
    <div className="overflow-auto rounded border"><div className="min-w-[760px]">
      <div className="grid grid-cols-[150px_1fr] border-b bg-slate-50 text-[10px] text-slate-500"><div className="p-2">资源</div><div className="flex justify-between p-2"><span>{fmt(rangeStart)}</span><span>{fmt(rangeEnd)}</span></div></div>
      <div className="relative">{rowModels.map(row =>
        <div key={row.resource} className="grid grid-cols-[150px_1fr] border-b last:border-0" style={{ minHeight: row.height }}><div className="truncate border-r p-2 text-xs font-medium" title={row.resource}>{row.label}</div>
          <div className="relative select-none bg-[linear-gradient(to_right,rgba(148,163,184,.18)_1px,transparent_1px)] bg-[length:12.5%_100%]" data-resource={row.resource} onMouseDown={event => createStart(event, row.resource)} onMouseUp={event => createEnd(event, row.resource)} onDragOver={event => event.preventDefault()} onDrop={event => drop(event, row.resource)}>
            {searchResult?.state === 'result' && searchResult.highlights.filter(item => item.type === 'SLOT' && item.resource === row.resource && item.end > rangeStart && item.start < rangeEnd).map((slot, index) => slot.type === 'SLOT' && <div key={`slot-${index}`} title={slot.comment || '推荐时间段'} className="absolute inset-y-0 border-x border-amber-500 bg-amber-300/35" style={{ left: `${Math.max(0, (slot.start - rangeStart) / span * 100)}%`, width: `${Math.max(0.5, (Math.min(slot.end, rangeEnd) - Math.max(slot.start, rangeStart)) / span * 100)}%` }} />)}
            {row.tasks.map(task => { const left = (task.start! - rangeStart) / span * 100, rawWidth = Math.max(1, (task.end! - task.start!) / span * 100), layer = layerMap.get(task.layer)!, width = layer.style === 'event' ? 0.6 : rawWidth; const bad = violations.some(issue => issue.taskIds.includes(task.id))
              const recommended = searchResult?.state === 'result' && searchResult.highlights.some(item => item.type === 'PUCK' && item.taskId === task.id)
              return <button key={task.id} data-task={task.id} draggable={!rt.editing && layer.style !== 'background'} aria-pressed={selectedTask === task.id} onMouseDown={event => event.stopPropagation()} onClick={() => { setSelectedTask(task.id); setSearchState({ signature: '', value: null }) }} onDragStart={event => startDrag(event, task.id)} title={`${layer.label} · ${task.label}\n${fmt(task.start!)} – ${fmt(task.end!)}`} className={`absolute h-9 overflow-hidden rounded px-2 text-left text-[10px] text-white shadow ${layer.style === 'background' ? 'opacity-45' : ''} ${recommended ? 'ring-4 ring-amber-300' : selectedTask === task.id ? 'ring-2 ring-violet-300' : ''} ${bad ? 'bg-rose-600' : overrides[task.id] || task.id.startsWith('draft-') ? 'bg-amber-500' : ''}`} style={{ zIndex: layer.style === 'background' ? 1 : 2, top: 8 + (row.layout.lanes.get(task.id) ?? 0) * 44, left: `${left}%`, width: `${Math.min(width, 100 - left)}%`, backgroundColor: bad ? undefined : overrides[task.id] || task.id.startsWith('draft-') ? undefined : layer.color }}><span className="block truncate font-semibold">{task.label}</span>{layer.style !== 'event' && <span className="block truncate opacity-80">{fmt(task.start!).slice(11)}–{fmt(task.end!).slice(11)}</span>}</button> })}
          </div></div>)}
        {showDependencies && dependencyArrows.length > 0 && <svg className="pointer-events-none absolute left-[150px] top-0 z-[3]" style={{ width: 'calc(100% - 150px)', height: accumulatedTop }} viewBox={`0 0 1000 ${accumulatedTop}`} preserveAspectRatio="none" aria-label="任务依赖箭头"><defs><marker id="schedule-arrow" markerWidth="7" markerHeight="7" refX="6" refY="3.5" orient="auto"><path d="M0,0 L7,3.5 L0,7 z" fill="currentColor" /></marker></defs>{dependencyArrows.map(edge => { const from = taskPoints.get(edge.from.id)!, to = taskPoints.get(edge.to.id)!, active = selectedTask === edge.from.id || selectedTask === edge.to.id; return <path key={`${edge.from.id}-${edge.to.id}`} d={`M ${from.endX} ${from.y} C ${from.endX + 20} ${from.y}, ${to.startX - 20} ${to.y}, ${to.startX} ${to.y}`} fill="none" stroke={active ? '#7c3aed' : '#64748b'} strokeWidth={active ? 3 : 1.5} vectorEffect="non-scaling-stroke" markerEnd="url(#schedule-arrow)" /> })}</svg>}
      </div>
    </div></div>
    <details open={unscheduled.length > 0} className="rounded border p-2"><summary className="cursor-pointer text-xs font-medium">未排任务（{unscheduled.length}）</summary><div className="mt-2 flex flex-wrap gap-2">{unscheduled.map(task => <button key={task.id} draggable={!rt.editing} aria-pressed={selectedTask === task.id} onClick={() => { setSelectedTask(task.id); setSearchState({ signature: '', value: null }) }} onDragStart={event => startDrag(event, task.id)} className={`rounded border border-dashed bg-white px-2 py-1 text-xs ${selectedTask === task.id ? 'ring-2 ring-violet-300' : ''}`}>{task.label}</button>)}</div></details>
    {!rt.editing && selectedTask && (() => { const task = tasks.find(row => row.id === selectedTask), step = number(p.snap_minutes, 15) * 60_000; if (!task) return null; const editable = layerMap.get(task.layer)?.style !== 'background'
      return <div className="flex flex-wrap items-center gap-2 rounded border bg-slate-50 p-2 text-xs" aria-label="所选任务排程控件"><strong>{task.label}</strong><label>资源 <select aria-label="所选任务资源" disabled={!editable} className="wb-input ml-1 py-1" value={task.resource} onChange={event => propose(task.id, { resource: event.target.value })}><option value="">未分配</option>{resources.map(([id, label]) => <option key={id} value={id}>{label}</option>)}</select></label>
        <button className="wb-button-secondary" disabled={!editable || task.start === null || task.end === null} onClick={() => propose(task.id, { start: task.start! - step, end: task.end! - step })}>提前一步</button><button className="wb-button-secondary" disabled={!editable || task.start === null || task.end === null} onClick={() => propose(task.id, { start: task.start! + step, end: task.end! + step })}>延后一步</button>
        <button className="wb-button-secondary" disabled={!editable || task.start === null || task.end === null || task.end - task.start <= step} onClick={() => propose(task.id, { end: task.end! - step })}>缩短一步</button><button className="wb-button-secondary" disabled={!editable || task.start === null || task.end === null} onClick={() => propose(task.id, { end: task.end! + step })}>延长一步</button></div> })()}
    {violations.length > 0 && <div className="rounded border border-rose-200 bg-rose-50 p-2 text-xs text-rose-800" role="alert"><strong>{violations.length} 项排程校验问题</strong><ul className="list-disc pl-5">{violations.slice(0, 20).map((issue, index) => <li key={`${issue.code}-${index}`}>{issue.message}</li>)}</ul></div>}
    {proposalCount > 0 && <details open className="rounded border p-2"><summary className="cursor-pointer text-xs font-medium">变更记录（{proposalCount}）</summary><ul className="mt-1 text-xs">{changed.map(([id, row]) => <li key={id}>{base.find(task => task.id === id)?.label} → {row.resources.map(resource => resourceLabels.get(resource) ?? resource).join('、')} · {fmt(row.start)}–{fmt(row.end)} <button className="ml-2 text-rose-700" onClick={() => { setOverrides(current => { const next = { ...current }; delete next[id]; return next }); setPreview(null) }}>撤销</button></li>)}{creates.map(row => <li key={row.id}><strong>新建</strong> {row.label} → {row.resources.map(resource => resourceLabels.get(resource) ?? resource).join('、')} · {fmt(row.start)}–{fmt(row.end)} <button className="ml-2 text-rose-700" onClick={() => { setCreates(current => current.filter(item => item.id !== row.id)); setPreview(null) }}>撤销</button></li>)}</ul></details>}
    {!rt.editing && text(p.recommendation_asset_id) && <div className="rounded border border-amber-200 bg-amber-50/50 p-2 text-xs"><div className="flex flex-wrap items-center gap-2"><button className="wb-button-secondary" disabled={!selectedTask || search.isPending} onClick={() => search.mutate()}>{search.isPending ? '正在计算推荐…' : '为所选任务查找推荐'}</button><span className="text-slate-600">{selectedTask ? `已选择：${tasks.find(task => task.id === selectedTask)?.label ?? selectedTask}` : '先选择一个任务'}</span></div>
      {searchResult?.state === 'error' && <p role="alert" className="mt-2 text-rose-700">推荐 Function：{searchResult.error}</p>}{searchResult?.state === 'empty' && <p className="mt-2 text-slate-600">推荐 Function 正常完成，但没有可用推荐。</p>}{searchResult?.state === 'result' && <p className="mt-2 text-amber-800"><strong>{searchResult.title}</strong> · {searchResult.highlights.filter(item => item.type === 'SLOT').length} 个时间段 · {searchResult.highlights.filter(item => item.type === 'PUCK').length} 个任务</p>}
      {search.error && <p role="alert" className="mt-2 text-rose-700">{formatApiError(search.error)}</p>}</div>}
    {!rt.editing && text(p.validation_asset_id) && <div className="rounded border border-violet-200 bg-violet-50/50 p-2 text-xs"><button className="wb-button-secondary" disabled={!proposalCount || blocked || businessCheck.isPending} onClick={() => businessCheck.mutate()}>{businessCheck.isPending ? '正在运行业务校验…' : '运行业务校验'}</button>
      {businessValidation?.state === 'error' && <p role="alert" className="mt-2 text-rose-700">业务校验 Function：{businessValidation.error}</p>}{businessValidation?.state === 'result' && <div className="mt-2"><strong>{businessValidation.violations.length ? `${businessValidation.violations.length} 项业务校验结果` : '业务校验通过'}</strong>{businessValidation.violations.length > 0 && <ul className="list-disc pl-5">{businessValidation.violations.map((issue, index) => <li key={`${issue.code}-${index}`} className={issue.severity === 'error' ? 'text-rose-700' : 'text-amber-700'}>{issue.message}</li>)}</ul>}</div>}{businessCheck.error && <p role="alert" className="mt-2 text-rose-700">{formatApiError(businessCheck.error)}</p>}</div>}
    {!rt.editing && <div className="flex flex-wrap items-center gap-2"><button className="wb-button-secondary" disabled={!proposalCount || blocked || businessBlocked || !canPersist || review.isPending} onClick={() => review.mutate()}>检查 Scenario 变更</button><button className="wb-button-primary" disabled={!preview || blocked || businessBlocked || save.isPending} onClick={() => save.mutate()}>保存到 Scenario</button>{preview && <span className="text-xs text-emerald-700">Action 预览：{preview.edit_count} 项本体修改</span>}{message && <span role="status" className="text-xs text-emerald-700">{message}</span>}</div>}
    {!canPersist && targetsReady && !rt.editing && <p className="text-xs text-amber-700">当前只可本地试排：请配置 Scenario 变量，以及修改/创建 Action 的参数绑定后再提交。</p>}
    {!targetsReady && !rt.editing && <p className="text-xs text-amber-700">目标对象参数已启用，但待保存任务缺少对象类型或对象 ID 绑定。</p>}
    {(review.error || save.error) && <p role="alert" className="text-xs text-rose-700">{formatApiError(review.error || save.error)}</p>}
  </section>
}
