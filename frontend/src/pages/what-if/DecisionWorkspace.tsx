import { useMemo, useState } from 'react'
import DecisionPackagePanel from './DecisionPackagePanel'
import { describe, plain, useRunPackage, type DecisionRunContext } from './decisionPackage'
import { formatStudyValue } from './studyDisplay'

type Subject = { id: string; label: string; status: string; at_risk?: boolean; metric_value?: number; reasons?: string[]; path?: string[]; evidence?: Array<{ label: string; value: unknown }> }
type Candidate = { id: string; label: string; applicable: boolean; hard_constraints: Array<{ code: string; [key: string]: unknown }>; metric_after: number; cost?: number; protected: string[]; harmed: string[]; actions: Array<{ action_type: string; parameters: Record<string, unknown> }> }
/** decision/v1: the domain-neutral result any decision Function returns. */
export type DecisionOutput = { schema: 'decision/v1'; title: string; subject_type: string; subject_label?: string; metric: { key: string; label: string; unit?: string; direction: 'minimize' | 'maximize'; baseline: number }; kpis?: Array<{ label: string; value: unknown }>; subjects: Subject[]; candidates: Candidate[] }

const number = (value: unknown) => typeof value === 'number' && Number.isFinite(value) ? value.toLocaleString('en-US', { maximumFractionDigits: 2 }) : formatStudyValue(value)

export default function DecisionWorkspace({ decision, context }: { decision: DecisionOutput; context?: DecisionRunContext }) {
  const subjects = useMemo(() => [...decision.subjects].sort((a, b) => Number(!!b.at_risk) - Number(!!a.at_risk) || a.label.localeCompare(b.label)), [decision.subjects])
  const names = useMemo(() => new Map(decision.subjects.map(row => [row.id, plain(row.label)])), [decision.subjects])
  const [selected, setSelected] = useState(subjects[0]?.id ?? '')
  const [chosenId, setChosenId] = useState<string | null>(null)
  const { pkg, candidateId: packageCandidate } = useRunPackage(context)
  const candidateId = packageCandidate ?? chosenId ?? decision.candidates.find(row => row.applicable)?.id ?? ''
  const candidate = decision.candidates.find(row => row.id === candidateId)
  const active = subjects.find(row => row.id === selected)
  const { metric } = decision
  const unit = metric.unit ? ` ${metric.unit}` : ''
  const better = (after: number) => metric.direction === 'minimize' ? metric.baseline - after : after - metric.baseline
  const list = (ids: string[]) => ids.map(id => names.get(id) ?? plain(id)).join('、') || '—'
  const subjectLabel = decision.subject_label ?? decision.subject_type
  const atRisk = subjects.filter(row => row.at_risk).length

  return <section className="decision-workspace" data-testid="decision-workspace">
    <header className="decision-workspace__header">
      <div><p className="wb-eyebrow">Decision workspace</p><h3>{decision.title}</h3><p>从{subjectLabel}的风险钻取到原因和证据，比较候选方案的获益与受损{subjectLabel}，再提交治理审核。</p></div>
      <span className={`decision-status ${atRisk ? 'is-risk' : 'is-good'}`}>{atRisk ? '需要决策' : '无风险'}</span>
    </header>
    <div className="decision-kpis">
      <article><span>{metric.label}</span><strong>{number(metric.baseline)}{unit}</strong></article>
      <article><span>风险{subjectLabel}</span><strong>{atRisk} / {subjects.length}</strong></article>
      {(decision.kpis ?? []).slice(0, 2).map(row => <article key={row.label}><span>{row.label}</span><strong>{number(row.value)}</strong></article>)}
    </div>
    <div className="decision-layout">
      <aside>
        <h4>{subjectLabel}</h4>
        {subjects.map(row => <button key={row.id} className={selected === row.id ? 'is-selected' : ''} onClick={() => setSelected(row.id)}>
          <span>{plain(row.label)}</span>
          <strong className={row.at_risk ? '' : 'is-ok'}>{row.status}{row.metric_value !== undefined && row.at_risk ? ` · ${number(row.metric_value)}${unit}` : ''}</strong>
        </button>)}
      </aside>
      <main>
        <div className="decision-panel">
          <h4>原因与证据</h4>
          {active ? <>
            {active.path?.length ? <div className="decision-path">{active.path.map((node, step) => <span key={`${node}-${step}`}>{step ? '→ ' : ''}{plain(node)}</span>)}</div> : null}
            {active.reasons?.length ? <ul className="decision-reasons">{active.reasons.map(reason => <li key={reason}>{reason}</li>)}</ul> : <p className="decision-muted">没有风险原因。</p>}
            {active.evidence?.length ? <table><tbody>{active.evidence.map(row => <tr key={row.label}><th>{row.label}</th><td>{number(row.value)}</td></tr>)}</tbody></table> : null}
          </> : <p>请选择{subjectLabel}。</p>}
        </div>
        <div className="decision-panel">
          <h4>候选方案比较</h4>
          {decision.candidates.length ? <table className="candidate-table"><thead><tr><th></th><th>方案</th><th>{metric.label}</th><th>改善</th><th>获益{subjectLabel}</th><th>受损{subjectLabel}</th><th>增量成本</th><th>约束</th></tr></thead>
            <tbody>
              <tr className="is-baseline"><td></td><td>基线（不采取动作）</td><td>{number(metric.baseline)}</td><td>—</td><td>—</td><td>—</td><td>0</td><td>—</td></tr>
              {decision.candidates.map(row => <tr key={row.id} className={candidateId === row.id ? 'is-selected' : ''} onClick={() => !pkg && setChosenId(row.id)}>
                <td><input type="radio" name="decision-candidate" aria-label={plain(row.label)} checked={candidateId === row.id} disabled={!!pkg} onChange={() => setChosenId(row.id)} /></td>
                <td>{plain(row.label)}</td><td>{number(row.metric_after)}</td>
                <td className={better(row.metric_after) > 0 ? 'is-gain' : better(row.metric_after) < 0 ? 'is-loss' : ''}>{number(better(row.metric_after))}</td>
                <td>{list(row.protected)}</td><td className={row.harmed.length ? 'is-loss' : ''}>{list(row.harmed)}</td>
                <td>{number(row.cost ?? 0)}</td>
                <td>{row.applicable ? '满足' : <em>{row.hard_constraints.map(item => item.code).join('、')}</em>}</td>
              </tr>)}
            </tbody></table> : <p className="decision-muted">当前没有需要执行的方案。</p>}
        </div>
      </main>
      <aside className="candidate-rail">
        <h4>变更预览</h4>
        {candidate ? <>
          <p className="candidate-title">{plain(candidate.label)}</p>
          {!candidate.applicable && <p className="candidate-warning">硬约束未满足，只能作为参考，不能提交执行。</p>}
          {candidate.harmed.length > 0 && <p className="candidate-warning">将使 {list(candidate.harmed)} 由无风险变为有风险。</p>}
          <ol className="candidate-actions">{candidate.actions.map((action, index) => <li key={index}><strong>{action.action_type}</strong><small>{describe(action.parameters)}</small></li>)}</ol>
          <DecisionPackagePanel context={context} candidate={candidate} />
        </> : <p className="decision-muted">选择一个候选方案查看变更。</p>}
      </aside>
    </div>
  </section>
}
