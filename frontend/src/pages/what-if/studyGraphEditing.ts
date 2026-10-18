import type { EditableStudySpec } from './StudySpecBuilder'

export function removeStudyEdges(spec: EditableStudySpec, indices: number[]): EditableStudySpec {
  const removed = spec.edges.filter((_, index) => indices.includes(index))
  return { ...spec, edges: spec.edges.filter((_, index) => !indices.includes(index)), nodes: spec.nodes.map(node => ({ ...node, inputs: Object.fromEntries(Object.entries((node.inputs ?? {}) as Record<string, unknown>).map(([port, binding]) => {
    const source = binding && typeof binding === 'object' ? (binding as Record<string, unknown>).from : undefined
    return [port, removed.some(edge => edge.target === `${node.key}.${port}` && edge.source === source) ? null : binding]
  })) })) }
}
