import { createContext, useContext, type Dispatch, type SetStateAction } from 'react'

export type ScenarioContextRef = {
  ontology_id: string
  study_id: string
  case_id: string
  case_key: string
  case_definition_revision: number
  compatibility_hash: string
  scenario_id: string
  scenario_revision: number
  revision_view_id: string
  selected_run_id: string | null
  selected_result_view_id: string | null
  selected_run_status: string | null
  input_digest: string | null
  output_digest: string | null
  selection_reason: string
  context_token: string
  view_id?: string
}

export type ScenarioContextCase = {
  id: string
  key: string
  context_ref?: ScenarioContextRef
}

export type ScenarioContextValue = {
  activeCase?: ScenarioContextCase
  activeContext?: ScenarioContextRef
  contextToken: string | null
  selectedObject: { type: string; id: string } | null
  setSelectedObject: Dispatch<SetStateAction<{ type: string; id: string } | null>>
  draftByCase: Record<string, unknown>
  setDraft: (caseId: string, value: unknown) => void
  clearDraft: (caseId: string) => void
}

export const ScenarioContext = createContext<ScenarioContextValue | null>(null)

export function useScenarioContext() {
  const value = useContext(ScenarioContext)
  if (!value) throw new Error('useScenarioContext must be used inside ScenarioContextProvider')
  return value
}

export function responseMatchesScenarioContext(response: { context?: Partial<ScenarioContextRef> } | undefined, context?: ScenarioContextRef) {
  return Boolean(response?.context && context && response.context.context_token === context.context_token)
}
