import { useCallback, useMemo } from 'react'
import { useQueries, useQuery, useQueryClient } from '@tanstack/react-query'
import { useSearchParams } from 'react-router-dom'
import { apiClientV2 } from '@/api/client'
import { DEFAULT_SETTINGS, type AppDefinition, type AppRecord, type DataSource, type ResolvedSource, type TimeRange } from './spec'

/** The row field the app-wide time range uses for a source (alerts always have their window end). */
export const timeFieldOf = (source: DataSource) => source.time_field ?? (source.kind === 'alerts' ? 'window_end' : null)
/** Study runs are filtered in the page; Ontology sources are filtered on the server (see useSources). */
export const timeFieldsOf = (definition: AppDefinition | undefined) =>
  Object.fromEntries((definition?.data_sources ?? []).map(source => [source.id, source.kind === 'study_run' ? source.time_field ?? null : null]))

export const appsUrl = (ontologyId: string) => `/ontologies/${ontologyId}/workshop-apps`

// A source without a time field always asks for everything, so changing the range does not refetch it.
const rangeFor = (source: DataSource, range: TimeRange) => (source.kind !== 'study_run' && timeFieldOf(source) ? range : 'all')

export function useApp(ontologyId: string, appId: string, version?: number) {
  return useQuery({ queryKey: ['workshop-app', ontologyId, appId, version ?? 'head'], enabled: !!ontologyId && !!appId,
    queryFn: () => apiClientV2.get<AppRecord>(`${appsUrl(ontologyId)}/${appId}`, { params: version ? { version } : undefined }) })
}

/**
 * Resolve every data source; a `run.<source>` URL parameter pins a run, otherwise the Study's latest completed run is used.
 * Ontology sources with a time field get the time range applied on the server, over all their objects.
 */
export function useSources(ontologyId: string, appId: string, definition: AppDefinition | undefined, pinned: Record<string, string | undefined>, version?: number, timeRange: TimeRange = 'all') {
  const queryClient = useQueryClient()
  const sources = definition?.data_sources ?? []
  const results = useQueries({ queries: sources.map(source => ({
    queryKey: ['workshop-source', ontologyId, appId, version ?? 'head', source.id, pinned[source.id] ?? 'latest', JSON.stringify(source), rangeFor(source, timeRange)],
    queryFn: () => apiClientV2.get<ResolvedSource>(`${appsUrl(ontologyId)}/${appId}/sources/${source.id}`, { params: { ...(pinned[source.id] ? { run: pinned[source.id] } : {}), ...(version ? { version } : {}), range: rangeFor(source, timeRange) } }),
    retry: false,
  })) })
  const resolved = Object.fromEntries(sources.map((source, index) => [source.id, results[index]?.data]))
  const errors = sources.map((source, index) => results[index]?.error ? source.id : null).filter(Boolean) as string[]
  const refresh = useCallback(() => queryClient.invalidateQueries({ queryKey: ['workshop-source', ontologyId, appId] }), [queryClient, ontologyId, appId])
  /** Take a new Ontology snapshot (object-set sources read it), then reload every source. */
  const usesSnapshot = sources.some(source => source.kind === 'object_set' || source.kind === 'aggregate')
  const reload = useCallback(async () => {
    if (usesSnapshot) await apiClientV2.post(`${appsUrl(ontologyId)}/${appId}/snapshot`)
    await refresh()
  }, [usesSnapshot, ontologyId, appId, refresh])
  return { resolved, errors, refresh, reload }
}

/** Variables live in the URL (v.<id>=value) so every state of an app is a shareable link. */
export function useUrlState(definition: AppDefinition | undefined) {
  const [params, setParams] = useSearchParams()
  const vars = useMemo(() => Object.fromEntries((definition?.variables ?? []).map(variable =>
    [variable.id, params.get(`v.${variable.id}`) ?? (variable.default === undefined || variable.default === null ? null : String(variable.default))])), [definition, params])
  const setVar = useCallback((id: string, value: string | null) => setParams(current => { if (value === null || value === '') current.delete(`v.${id}`); else current.set(`v.${id}`, value); return current }), [setParams])
  const pageId = params.get('page') ?? definition?.pages[0]?.id ?? ''
  const navigate = useCallback((page: string) => setParams(current => { current.set('page', page); return current }), [setParams])
  const pinned = useMemo(() => Object.fromEntries((definition?.data_sources ?? []).map(source => [source.id, params.get(`run.${source.id}`) ?? undefined])), [definition, params])
  // App-wide controls: the URL wins, then the app's saved defaults.
  const settings = definition?.settings ?? DEFAULT_SETTINGS
  const timeRange = (params.get('t') ?? settings.time_range) as TimeRange
  const refreshMinutes = Number(params.get('r') ?? settings.refresh_minutes)
  const setControl = useCallback((key: 't' | 'r', value: string) => setParams(current => { current.set(key, value); return current }), [setParams])
  return { vars, setVar, pageId, navigate, pinned, params, timeRange, refreshMinutes, setControl }
}
