import { useMemo, useState } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClientV2, formatApiError } from '@/api/client'
import { formatStudyValue } from './studyDisplay'

type Shortage = { demand_id: string; item: string; quantity: number; path: string[] }
type Source = { item: string; source: string; source_id: string; quantity: number; available_at: string; location?: string; supplier?: string }
type Impact = { demand_id: string; item: string; quantity: number; due_at?: string; finish_at?: string | null; late_days: number; status: 'on_time' | 'late' | 'blocked'; revenue: number; revenue_at_risk: number; resource?: string | null; shortages: Shortage[]; used_sources: Source[]; root_cause_items: string[]; reason_codes: string[] }
type OrderRef = { demand_id: string; revenue: number; status?: string; late_days?: number }
type CandidateAction = { action_type: string; [key: string]: unknown }
type Candidate = { id: string; kind: string; item: string; label: string; supplier?: string; applicable: boolean; hard_constraints: Array<{ code: string; subject: string }>; revenue_at_risk: number; revenue_protected: number; protected_orders: OrderRef[]; harmed_orders: OrderRef[]; incremental_cost: number; actions: CandidateAction[]; content_digest: string }
type Path = { schema: string; nodes: Array<{ id: string; kind: string; label: string }>; edges: Array<{ source: string; target: string; kind: string }>; completeness: string; content_digest: string }
type Kpis = { revenue_at_risk: number; at_risk_orders: number; blocked_orders: number; late_orders: number; on_time_units: number; demanded_units: number; root_cause_items: string[]; stranded_reserved_quantity: number }
export type MaterialDecisionOutputs = { feasible: boolean; revenue_at_risk: number; kpis?: Kpis; demand_impacts: Impact[]; shortages: Shortage[]; candidate_actions: Candidate[]; impact_path: Path }
export type DecisionRunContext = { ontologyId: string; runId: string; scenarioId: string; scenarioRevision: number; nodeKey: string; artifact?: { id: string; content_digest: string } }
type PackageEdit = { sequence: number; op: string; target?: { concrete_type: string; object_id: string } | null; property?: string | null; value?: unknown; expected_old_value?: unknown; properties?: Record<string, unknown> }
type PackageAction = { id: string; action_type_id: string; expected_edits: PackageEdit[]; external_effect_class: string; receipt: Record<string, unknown> | null }
type Package = { id: string; run_id: string; evidence: Array<{ reference: { candidate_id?: string } }>; status: string; etag: number; created_by: string; title: string; actions: PackageAction[]; outbox: Array<{ id: string; delivered: boolean; attempts: number }>; events: Array<{ sequence: number; event_type: string; actor_id: string; reason: string }> }
type Delivery = { external_effects: number; delivered: number; status: 'complete' | 'external_effect_partial' }
const editLabel: Record<string, string> = { set_property: '修改', create_object: '新建', delete_object: '删除', unset_property: '清除' }

const statusLabel: Record<Impact['status'], string> = { on_time: '按期', late: '迟交', blocked: '缺件阻塞' }
const reasonLabel: Record<string, string> = { material_shortage: '缺件', late_material: '物料晚到', capacity_conflict: '产能冲突' }
const actionLabel: Record<string, string> = { expedite_supply_commitment: '加急供应承诺', create_purchase_order: '创建采购单', qualify_supplier: '认证供应商', reprioritize_allocation: '调整分配优先级', reschedule_work_orders: '重新排程', reassign_work_order: '工单换线' }
const externalActions = new Set(['create_purchase_order', 'expedite_supply_commitment'])
const packageStatus: Record<string, string> = { draft: '草稿', in_review: '审核中', approved: '已批准', rejected: '已驳回', applying: '执行中', applied: '已执行', apply_failed: '执行失败', reverted: '已回滚', superseded: '已替代' }

// Seeded object ids carry an ontology namespace ("5ef77329:ORD-1009"); show the business id.
const plain = (text: string) => text.replace(/\b[0-9a-f]{8}:/g, '')
const orderList = (rows: OrderRef[]) => rows.map(order => plain(order.demand_id)).join('、') || '—'

function describe(action: CandidateAction) {
  return Object.entries(action).filter(([key]) => key !== 'action_type').filter(([, value]) => value !== null && value !== undefined && !(Array.isArray(value) && !value.length)).map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(',') : String(value)}`).join(' · ').replace(/\b[0-9a-f]{8}:/g, '')
}

function money(value: number) {
  return Number.isFinite(value) ? Math.round(value).toLocaleString('en-US') : formatStudyValue(value)
}

export default function MaterialDecisionWorkspace({ outputs, context }: { outputs: MaterialDecisionOutputs; context?: DecisionRunContext }) {
  const impacts = useMemo(() => [...outputs.demand_impacts].sort((a, b) => Number(b.revenue_at_risk > 0) - Number(a.revenue_at_risk > 0) || a.demand_id.localeCompare(b.demand_id)), [outputs.demand_impacts])
  const [selected, setSelected] = useState(impacts.find(row => row.revenue_at_risk > 0)?.demand_id ?? impacts[0]?.demand_id ?? '')
  const [chosenId, setChosenId] = useState<string | null>(null)
  const [rationale, setRationale] = useState('')
  const [reviewReason, setReviewReason] = useState('')
  // The run's package is server state: it survives refreshes and reflects reviews done by other people.
  const queryClient = useQueryClient()
  const packagesKey = ['decision-packages', context?.ontologyId]
  const packages = useQuery({ queryKey: packagesKey, queryFn: () => apiClientV2.get<{ packages: Package[] }>(`/ontologies/${context!.ontologyId}/decision-packages`), enabled: !!context, refetchInterval: 5000 })
  const pkg = packages.data?.packages.find(row => row.run_id === context?.runId && row.status !== 'superseded') ?? null
  const setPkg = (row: Package) => queryClient.setQueryData<{ packages: Package[] }>(packagesKey, current => ({ packages: [row, ...(current?.packages ?? []).filter(item => item.id !== row.id)] }))
  const packageCandidate = pkg?.evidence.find(row => row.reference.candidate_id)?.reference.candidate_id
  const candidateId = packageCandidate ?? chosenId ?? outputs.candidate_actions.find(row => row.applicable)?.id ?? ''
  const setCandidateId = (id: string) => setChosenId(id)
  const [delivery, setDelivery] = useState<Delivery | null>(null)
  const active = impacts.find(row => row.demand_id === selected)
  const candidate = outputs.candidate_actions.find(row => row.id === candidateId)
  const kpis = outputs.kpis
  const paths = active ? (active.shortages.length ? active.shortages.map(row => row.path) : active.used_sources.filter(row => active.root_cause_items.includes(row.item)).map(row => (row as Source & { path?: string[] }).path ?? [row.item])) : []

  const packagesUrl = `/ontologies/${context?.ontologyId}/decision-packages`
  // The server compiles the candidate into typed edits from the run's pinned data view.
  const createPackage = useMutation({
    mutationFn: () => apiClientV2.post<Package>(`/ontologies/${context!.ontologyId}/scenario-study-runs/${context!.runId}/decision-packages`, { candidate_id: candidate!.id, rationale: rationale.trim() }),
    onSuccess: setPkg,
  })
  const transition = useMutation({
    mutationFn: (event: 'submit' | 'approve' | 'reject' | 'revise') => apiClientV2.post<Package>(`${packagesUrl}/${pkg!.id}/transition`, { event, expected_etag: pkg!.etag, request_id: crypto.randomUUID(), reason: reviewReason.trim() }),
    onSuccess: setPkg,
  })
  // One request id per click; a retried request is answered from the recorded event.
  const command = useMutation({
    mutationFn: (verb: 'apply' | 'revert' | 'deliver-outbox') => apiClientV2.post<{ package: Package; delivery?: Delivery; irreversible_effects?: Array<{ action_type: string; delivered: boolean }> }>(`${packagesUrl}/${pkg!.id}:${verb}`, verb === 'deliver-outbox' ? undefined : { expected_etag: pkg!.etag, request_id: crypto.randomUUID() }),
    onSuccess: result => { setPkg(result.package); if (result.delivery) setDelivery(result.delivery) },
  })
  const error = createPackage.error || transition.error || command.error
  const canCreate = !!candidate?.applicable && !!context && rationale.trim().length > 0 && !pkg
  const edits = pkg?.actions.flatMap(action => action.expected_edits.map(edit => ({ action, edit }))) ?? []

  return <section className="decision-workspace" data-testid="material-decision-workspace">
    <header className="decision-workspace__header">
      <div><p className="wb-eyebrow">Decision workspace</p><h3>缺件影响与收入保护</h3><p>从风险订单钻取到 BOM 根因，比较候选动作的获益与受损订单，再提交治理审核。</p></div>
      <span className={`decision-status ${outputs.feasible && !outputs.revenue_at_risk ? 'is-good' : 'is-risk'}`}>{outputs.revenue_at_risk ? '需要决策' : '供应可行'}</span>
    </header>
    <div className="decision-kpis">
      <article><span>风险收入</span><strong>{money(outputs.revenue_at_risk)}</strong></article>
      <article><span>受影响订单</span><strong>{kpis ? `${kpis.at_risk_orders}（阻塞 ${kpis.blocked_orders} · 迟交 ${kpis.late_orders}）` : impacts.filter(row => row.revenue_at_risk > 0).length}</strong></article>
      <article><span>按期可交付数量</span><strong>{kpis ? `${kpis.on_time_units} / ${kpis.demanded_units}` : '—'}</strong></article>
      <article><span>根因物料</span><strong>{kpis?.root_cause_items.join('、') || '—'}</strong></article>
    </div>
    <div className="decision-layout">
      <aside>
        <h4>订单</h4>
        {impacts.map(row => <button key={row.demand_id} className={selected === row.demand_id ? 'is-selected' : ''} onClick={() => setSelected(row.demand_id)}>
          <span>{plain(row.demand_id)} · {row.item} × {row.quantity}</span>
          <strong className={row.revenue_at_risk ? '' : 'is-ok'}>{row.revenue_at_risk ? `${statusLabel[row.status]} · ${money(row.revenue_at_risk)}` : statusLabel[row.status]}</strong>
        </button>)}
      </aside>
      <main>
        <div className="decision-panel">
          <h4>影响路径</h4>
          {active ? <>
            {paths.length ? paths.map((path, index) => <div className="decision-path" key={index}><span>{plain(active.demand_id)}</span>{path.map((node, step) => <span key={`${node}-${step}`}>→ {node}</span>)}</div>) : <p className="decision-muted">该订单在当前时间窗口内按期交付，没有根因路径。</p>}
            <table><thead><tr><th>状态</th><th>交期</th><th>预计完工</th><th>产线</th><th>原因</th><th>缺口</th></tr></thead>
              <tbody><tr><td>{statusLabel[active.status]}{active.late_days ? ` ${active.late_days} 天` : ''}</td><td>{active.due_at?.slice(0, 10)}</td><td>{active.finish_at?.slice(0, 10) ?? '无法完工'}</td><td>{active.resource ?? '—'}</td><td>{active.reason_codes.map(code => reasonLabel[code] ?? code).join('、') || '—'}</td><td>{active.shortages.map(row => `${row.item} ${row.quantity}`).join('、') || '—'}</td></tr></tbody>
            </table>
          </> : <p>请选择订单。</p>}
        </div>
        <div className="decision-panel">
          <h4>库存与在途证据</h4>
          {active?.used_sources.length ? <table><thead><tr><th>物料</th><th>来源</th><th>编号</th><th>数量</th><th>地点/供应商</th><th>可用时间</th></tr></thead>
            <tbody>{active.used_sources.filter(row => active.root_cause_items.includes(row.item) || row.source !== 'inventory').map((row, index) => <tr key={`${row.source_id}-${row.item}-${index}`} className={active.root_cause_items.includes(row.item) ? 'is-root' : ''}><td>{row.item}</td><td>{row.source}</td><td>{plain(row.source_id)}</td><td>{row.quantity}</td><td>{row.location ?? row.supplier ?? ''}</td><td>{row.available_at.slice(0, 10)}</td></tr>)}</tbody>
          </table> : <p className="decision-muted">{active?.status === 'blocked' ? '缺件订单不占用任何物料，已回退的分配可供其他订单使用。' : '仅使用现有自由库存。'}</p>}
        </div>
        <div className="decision-panel">
          <h4>候选方案比较</h4>
          {outputs.candidate_actions.length ? <table className="candidate-table"><thead><tr><th></th><th>方案</th><th>剩余风险收入</th><th>保护收入</th><th>获益订单</th><th>受损订单</th><th>增量成本</th><th>约束</th></tr></thead>
            <tbody>
              <tr className="is-baseline"><td></td><td>基线（不采取动作）</td><td>{money(outputs.revenue_at_risk)}</td><td>—</td><td>—</td><td>—</td><td>0</td><td>—</td></tr>
              {outputs.candidate_actions.map(row => <tr key={row.id} className={candidateId === row.id ? 'is-selected' : ''} onClick={() => !pkg && setCandidateId(row.id)}>
                <td><input type="radio" name="candidate" aria-label={plain(row.label)} checked={candidateId === row.id} disabled={!!pkg} onChange={() => setCandidateId(row.id)} /></td>
                <td>{plain(row.label)}</td><td>{money(row.revenue_at_risk)}</td><td className={row.revenue_protected > 0 ? 'is-gain' : ''}>{money(row.revenue_protected)}</td>
                <td>{orderList(row.protected_orders)}</td>
                <td className={row.harmed_orders.length ? 'is-loss' : ''}>{orderList(row.harmed_orders)}</td>
                <td>{money(row.incremental_cost)}</td>
                <td>{row.applicable ? '满足' : <em>{row.hard_constraints.map(item => item.code).join('、')}</em>}</td>
              </tr>)}
            </tbody></table> : <p className="decision-muted">当前没有需要执行的补救动作。</p>}
        </div>
      </main>
      <aside className="candidate-rail">
        <h4>变更预览</h4>
        {candidate ? <>
          <p className="candidate-title">{plain(candidate.label)}</p>
          {!candidate.applicable && <p className="candidate-warning">硬约束未满足，只能作为参考，不能提交执行。</p>}
          {candidate.harmed_orders.length > 0 && <p className="candidate-warning">将使 {orderList(candidate.harmed_orders)} 失去按期交付（{money(candidate.harmed_orders.reduce((sum, order) => sum + order.revenue, 0))}）。</p>}
          <ol className="candidate-actions">{candidate.actions.map((action, index) => <li key={index}><strong>{actionLabel[action.action_type] ?? action.action_type}{externalActions.has(action.action_type) ? ' · 外部不可逆' : ''}</strong><small>{describe(action)}</small></li>)}</ol>
          {!pkg ? <>
            <label className="candidate-field">决策理由<textarea value={rationale} onChange={event => setRationale(event.target.value)} rows={3} placeholder="为什么选择这个方案，以及接受哪些代价" /></label>
            <button className="wb-button-primary" disabled={!canCreate || createPackage.isPending} onClick={() => createPackage.mutate()}>创建决策包</button>
            {!context && <p className="candidate-note">运行完成后才能创建决策包。</p>}
          </> : <div className="package-panel" data-testid="decision-package">
            <p><strong>{packageStatus[pkg.status] ?? pkg.status}</strong> · 版本 {pkg.etag}</p>
            <p className="candidate-note">服务端编译的本体变更（{edits.length} 项，执行时会重新编译并核对摘要）：</p>
            <ol className="candidate-actions" data-testid="package-edits">{edits.map(({ action, edit }) => <li key={edit.sequence}><strong>{editLabel[edit.op] ?? edit.op} · {edit.target?.concrete_type} {plain(edit.target?.object_id ?? '')}</strong><small>{edit.property ? plain(`${edit.property}: ${String(edit.expected_old_value ?? '空')} → ${String(edit.value ?? '空')}`) : describe({ action_type: action.action_type_id, ...edit.properties })}</small></li>)}</ol>
            {pkg.status === 'draft' && <button className="wb-button-primary" disabled={transition.isPending} onClick={() => transition.mutate('submit')}>提交审核</button>}
            {pkg.status === 'in_review' && <>
              <label className="candidate-field">审核意见<textarea value={reviewReason} onChange={event => setReviewReason(event.target.value)} rows={2} placeholder="驳回时必填" /></label>
              <div className="package-buttons"><button className="wb-button-primary" disabled={transition.isPending} onClick={() => transition.mutate('approve')}>批准</button><button className="wb-button-secondary" disabled={transition.isPending || !reviewReason.trim()} onClick={() => transition.mutate('reject')}>驳回</button></div>
            </>}
            {pkg.status === 'rejected' && <button className="wb-button-secondary" disabled={transition.isPending} onClick={() => transition.mutate('revise')}>退回草稿修改</button>}
            {(pkg.status === 'approved' || pkg.status === 'apply_failed') && <button className="wb-button-primary" disabled={command.isPending} onClick={() => command.mutate('apply')}>{pkg.status === 'apply_failed' ? '重试执行' : '执行（合并到本体）'}</button>}
            {pkg.status === 'applied' && <>
              <ul className="package-receipts" data-testid="package-receipts">{pkg.actions.filter(action => action.external_effect_class !== 'none').map(action => <li key={action.id}>{actionLabel[action.action_type_id] ?? action.action_type_id} · {action.receipt ? `回执 ${String(action.receipt.external_id)}` : '待发送'}</li>)}</ul>
              {delivery?.status === 'external_effect_partial' && <p className="candidate-warning">本体变更已合并，但有 {delivery.external_effects - delivery.delivered} 项外部通知未送达。</p>}
              <div className="package-buttons">
                {pkg.outbox.some(row => !row.delivered) && <button className="wb-button-secondary" disabled={command.isPending} onClick={() => command.mutate('deliver-outbox')}>重发外部通知</button>}
                <button className="wb-button-secondary" disabled={command.isPending} onClick={() => command.mutate('revert')}>回滚本体变更</button>
              </div>
            </>}
            {pkg.status === 'reverted' && pkg.actions.some(action => action.receipt) && <p className="candidate-warning">本体变更已回滚；已发送给供应商的加急/采购通知无法自动撤回，需要人工跟进。</p>}
            <ol className="package-events">{pkg.events.map(row => <li key={row.sequence}>{row.event_type} · {row.actor_id.slice(0, 8)}{row.reason ? ` · ${row.reason}` : ''}</li>)}</ol>
          </div>}
          {error && <p role="alert" className="candidate-warning">{formatApiError(error)}</p>}
          <p className="candidate-note">提交后引用的运行摘要和情景版本不可变；创建者不能审批自己的方案。</p>
        </> : <p className="decision-muted">选择一个候选方案查看变更。</p>}
      </aside>
    </div>
    <footer>证据摘要 {outputs.impact_path.content_digest.slice(0, 16)} · {outputs.impact_path.schema} · {outputs.impact_path.completeness === 'exact' ? '完整' : '部分'}</footer>
  </section>
}
