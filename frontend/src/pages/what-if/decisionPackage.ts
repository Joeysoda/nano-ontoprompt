import { useQuery, useQueryClient } from '@tanstack/react-query'
import { apiClientV2 } from '@/api/client'

export type DecisionRunContext = { ontologyId: string; runId: string; scenarioId: string; scenarioRevision: number; nodeKey: string; artifact?: { id: string; content_digest: string } }
export type PackageEdit = { sequence: number; op: string; target?: { concrete_type: string; object_id: string } | null; property?: string | null; value?: unknown; expected_old_value?: unknown; properties?: Record<string, unknown> }
export type PackageAction = { id: string; action_type_id: string; expected_edits: PackageEdit[]; external_effect_class: string; receipt: Record<string, unknown> | null }
export type DecisionPackage = { id: string; run_id: string; evidence: Array<{ reference: { candidate_id?: string } }>; status: string; etag: number; created_by: string; title: string; actions: PackageAction[]; outbox: Array<{ id: string; delivered: boolean; attempts: number }>; events: Array<{ sequence: number; event_type: string; actor_id: string; reason: string }> }
export type Delivery = { external_effects: number; delivered: number; status: 'complete' | 'external_effect_partial' }

// Seeded object ids may carry an ontology namespace ("5ef77329:ORD-1009"); show the business id.
export const plain = (text: string) => text.replace(/\b[0-9a-f]{8}:/g, '')

export function describe(values: Record<string, unknown>) {
  return plain(Object.entries(values).filter(([key, value]) => key !== 'action_type' && value !== null && value !== undefined && !(Array.isArray(value) && !value.length))
    .map(([key, value]) => `${key}=${Array.isArray(value) ? value.join(',') : typeof value === 'object' ? String((value as { object_id?: string }).object_id ?? JSON.stringify(value)) : String(value)}`).join(' · '))
}

/** The run's package is server state: it survives refreshes and reflects reviews done by other people. */
export function useRunPackage(context?: DecisionRunContext) {
  const queryClient = useQueryClient()
  const key = ['decision-packages', context?.ontologyId]
  const packages = useQuery({ queryKey: key, queryFn: () => apiClientV2.get<{ packages: DecisionPackage[] }>(`/ontologies/${context!.ontologyId}/decision-packages`), enabled: !!context, refetchInterval: 5000 })
  const pkg = packages.data?.packages.find(row => row.run_id === context?.runId && row.status !== 'superseded') ?? null
  const setPkg = (row: DecisionPackage) => queryClient.setQueryData<{ packages: DecisionPackage[] }>(key, current => ({ packages: [row, ...(current?.packages ?? []).filter(item => item.id !== row.id)] }))
  return { pkg, setPkg, candidateId: pkg?.evidence.find(row => row.reference.candidate_id)?.reference.candidate_id }
}

