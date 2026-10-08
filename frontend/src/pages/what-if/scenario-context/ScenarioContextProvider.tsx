import { useMemo, useState, type ReactNode } from 'react'
import { ScenarioContext, type ScenarioContextCase, type ScenarioContextValue } from './scenarioContext'

export function ScenarioContextProvider({
  cases,
  activeKey,
  children,
}: {
  cases: ScenarioContextCase[]
  activeKey: string
  children: ReactNode
}) {
  const [selectedObject, setSelectedObject] = useState<{ type: string; id: string } | null>(null)
  const [draftByCase, setDraftByCase] = useState<Record<string, unknown>>({})
  const activeCase = cases.find(item => item.key === activeKey) ?? cases[0]
  const activeContext = activeCase?.context_ref
  const value = useMemo<ScenarioContextValue>(() => ({
    activeCase,
    activeContext,
    contextToken: activeContext?.context_token ?? null,
    selectedObject,
    setSelectedObject,
    draftByCase,
    setDraft: (caseId, draft) => setDraftByCase(current => ({ ...current, [caseId]: draft })),
    clearDraft: caseId => setDraftByCase(current => {
      const next = { ...current }
      delete next[caseId]
      return next
    }),
  }), [activeCase, activeContext, draftByCase, selectedObject])
  return <ScenarioContext.Provider value={value}>{children}</ScenarioContext.Provider>
}
