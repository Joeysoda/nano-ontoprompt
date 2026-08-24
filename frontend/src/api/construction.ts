import { apiClientV2 } from './client'

export const constructionApi = {
  createRun: (ontologyId: string, body: { mode: string; dataset_id?: string; model_name?: string; config?: object }) =>
    apiClientV2.post(`/ontologies/${ontologyId}/construction-runs`, body),
  getRun: (runId: string) => apiClientV2.get(`/construction-runs/${runId}`),
  listEvidence: (runId: string) => apiClientV2.get(`/construction-runs/${runId}/evidence`),
  provenance: (assertionId: string, ontologyId?: string) => apiClientV2.get(`/assertions/${assertionId}/provenance`, { params: { ontology_id: ontologyId } }),
  benchmark: (body: object) => apiClientV2.post('/benchmarks/runs', body),
}
