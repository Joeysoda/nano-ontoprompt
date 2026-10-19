import { useState } from 'react'
import { useMutation } from '@tanstack/react-query'
import { apiClientV2, formatApiError } from '@/api/client'
import { describe, plain, useRunPackage, type DecisionPackage, type DecisionRunContext, type Delivery } from './decisionPackage'

const editLabel: Record<string, string> = { set_property: '修改', create_object: '新建', delete_object: '删除', unset_property: '清除' }
const packageStatus: Record<string, string> = { draft: '草稿', in_review: '审核中', approved: '已批准', rejected: '已驳回', applying: '执行中', applied: '已执行', apply_failed: '执行失败', reverted: '已回滚', superseded: '已替代' }

export default function DecisionPackagePanel({ context, candidate, actionLabel = {} }: { context?: DecisionRunContext; candidate: { id: string; applicable: boolean }; actionLabel?: Record<string, string> }) {
  const { pkg, setPkg } = useRunPackage(context)
  const [rationale, setRationale] = useState('')
  const [reviewReason, setReviewReason] = useState('')
  const [delivery, setDelivery] = useState<Delivery | null>(null)
  const packagesUrl = `/ontologies/${context?.ontologyId}/decision-packages`
  // The server compiles the candidate into typed edits from the run's pinned data view.
  const createPackage = useMutation({
    mutationFn: () => apiClientV2.post<DecisionPackage>(`/ontologies/${context!.ontologyId}/scenario-study-runs/${context!.runId}/decision-packages`, { candidate_id: candidate.id, rationale: rationale.trim() }),
    onSuccess: setPkg,
  })
  const transition = useMutation({
    mutationFn: (event: 'submit' | 'approve' | 'reject' | 'revise') => apiClientV2.post<DecisionPackage>(`${packagesUrl}/${pkg!.id}/transition`, { event, expected_etag: pkg!.etag, request_id: crypto.randomUUID(), reason: reviewReason.trim() }),
    onSuccess: setPkg,
  })
  // One request id per click; a retried request is answered from the recorded event.
  const command = useMutation({
    mutationFn: (verb: 'apply' | 'revert' | 'deliver-outbox') => apiClientV2.post<{ package: DecisionPackage; delivery?: Delivery }>(`${packagesUrl}/${pkg!.id}:${verb}`, verb === 'deliver-outbox' ? undefined : { expected_etag: pkg!.etag, request_id: crypto.randomUUID() }),
    onSuccess: result => { setPkg(result.package); if (result.delivery) setDelivery(result.delivery) },
  })
  const error = createPackage.error || transition.error || command.error
  const edits = pkg?.actions.flatMap(action => action.expected_edits.filter(edit => edit.op !== 'invoke_action').map(edit => ({ action, edit }))) ?? []
  const label = (key: string) => actionLabel[key] ?? key

  return <>
    {!pkg ? <>
      <label className="candidate-field">决策理由<textarea value={rationale} onChange={event => setRationale(event.target.value)} rows={3} placeholder="为什么选择这个方案，以及接受哪些代价" /></label>
      <button className="wb-button-primary" disabled={!candidate.applicable || !context || !rationale.trim() || createPackage.isPending} onClick={() => createPackage.mutate()}>创建决策包</button>
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
        <ul className="package-receipts" data-testid="package-receipts">{pkg.actions.filter(action => action.external_effect_class !== 'none').map(action => <li key={action.id}>{label(action.action_type_id)} · {action.receipt ? `回执 ${String(action.receipt.external_id)}` : '待发送'}</li>)}</ul>
        {delivery?.status === 'external_effect_partial' && <p className="candidate-warning">本体变更已合并，但有 {delivery.external_effects - delivery.delivered} 项外部通知未送达。</p>}
        <div className="package-buttons">
          {pkg.outbox.some(row => !row.delivered) && <button className="wb-button-secondary" disabled={command.isPending} onClick={() => command.mutate('deliver-outbox')}>重发外部通知</button>}
          <button className="wb-button-secondary" disabled={command.isPending} onClick={() => command.mutate('revert')}>回滚本体变更</button>
        </div>
      </>}
      {pkg.status === 'reverted' && pkg.actions.some(action => action.receipt) && <p className="candidate-warning">本体变更已回滚；已发出的外部通知无法自动撤回，需要人工跟进。</p>}
      <ol className="package-events">{pkg.events.map(row => <li key={row.sequence}>{row.event_type} · {row.actor_id.slice(0, 8)}{row.reason ? ` · ${row.reason}` : ''}</li>)}</ol>
    </div>}
    {error && <p role="alert" className="candidate-warning">{formatApiError(error)}</p>}
    <p className="candidate-note">提交后引用的运行摘要和情景版本不可变；创建者不能审批自己的方案。</p>
  </>
}
