import { useState } from 'react'
import { Background, Controls, Handle, Position, ReactFlow, applyNodeChanges, type Node, type NodeProps, type Connection } from '@xyflow/react'
import '@xyflow/react/dist/style.css'
import type { EditableStudySpec } from './StudySpecBuilder'
import { nodeKinds } from './studyNodeKinds'
import { removeStudyEdges } from './studyGraphEditing'

const record = (value: unknown): Record<string, unknown> => value && typeof value === 'object' ? value as Record<string, unknown> : {}

function ModelNode({ data, selected }: NodeProps) {
  const model = record(data.model)
  const inputs = Object.keys(record(model.inputs)), outputs = Object.keys(record(model.outputs))
  const kind = nodeKinds[String(model.kind)]
  return <div className={`w-60 rounded border bg-white shadow-sm ${selected ? 'border-blue-500 ring-1 ring-blue-500' : 'border-slate-300'}`}>
    <div className="border-b px-4 py-3"><div className="text-[10px] uppercase tracking-wider text-slate-500">{kind?.label ?? String(model.kind)}</div><strong className="text-sm">{String(model.key)}</strong><p className="mt-1 truncate text-xs text-slate-500">{kind?.logic ? String(record(model.implementation).key || '待绑定逻辑 / 动作') : '内置计算'}</p></div>
    <div className="py-2">{inputs.map(port => <div key={`in:${port}`} className="relative px-4 py-1 text-xs text-slate-600"><Handle type="target" position={Position.Left} id={port} aria-label={`${model.key} 输入 ${port}`} />{port}</div>)}{outputs.map(port => <div key={`out:${port}`} className="relative px-4 py-1 text-right text-xs text-blue-700">{port}<Handle type="source" position={Position.Right} id={port} aria-label={`${model.key} 输出 ${port}`} /></div>)}</div>
  </div>
}
const nodeTypes = { model: ModelNode }

export default function StudyGraphCanvas({ spec, onChange, selectedKey, onSelect, scope, readOnly = false }: { spec: EditableStudySpec; onChange: (spec: EditableStudySpec) => void; selectedKey: string; onSelect: (key: string) => void; scope: string; readOnly?: boolean }) {
  const storageKey = `study-graph-layout:${scope}`
  const [positions, setPositions] = useState<Record<string, { x: number; y: number }>>(() => { try { return JSON.parse(localStorage.getItem(storageKey) ?? '{}') } catch { return {} } })
  const [message, setMessage] = useState('')
  const nodes: Node[] = spec.nodes.map((model, index) => ({ id: String(model.key), type: 'model', deletable: false, position: positions[String(model.key)] ?? { x: 60 + (index % 3) * 320, y: 60 + Math.floor(index / 3) * 280 }, selected: selectedKey === model.key, data: { model } }))
  const edges = spec.edges.map((edge, index) => { const [source, ...sourcePort] = String(edge.source).split('.'), [target, ...targetPort] = String(edge.target).split('.'); return { id: `edge-${index}`, source, target, sourceHandle: sourcePort.join('.'), targetHandle: targetPort.join('.'), style: { stroke: '#64748b', strokeWidth: 1.5 } } })
  const valid = (connection: Connection) => {
    if (!connection.sourceHandle || !connection.targetHandle || connection.source === connection.target) return false
    const visit = (key: string, seen = new Set<string>()): boolean => { if (key === connection.source) return true; if (seen.has(key)) return false; seen.add(key); return edges.filter(edge => edge.source === key).some(edge => visit(edge.target, seen)) }
    return !visit(connection.target)
  }
  return <div className="relative h-[440px] overflow-hidden border border-slate-200 bg-slate-50" aria-label="可视化计算图">
    <ReactFlow nodes={nodes} edges={edges} nodeTypes={nodeTypes} fitView fitViewOptions={{ maxZoom: 1, padding: 0.2 }} minZoom={0.3} maxZoom={1.6} nodesConnectable={!readOnly} onNodeClick={(_, node) => onSelect(node.id)} onNodesChange={changes => {
      const moved = applyNodeChanges(changes, nodes)
      if (changes.some(change => change.type === 'position')) { const next = Object.fromEntries(moved.map(node => [node.id, node.position])); setPositions(next); try { localStorage.setItem(storageKey, JSON.stringify(next)) } catch { /* Layout is optional. */ } }
    }} isValidConnection={connection => valid(connection as Connection)} onConnect={connection => {
      if (readOnly || !valid(connection)) return
      const target = `${connection.target}.${connection.targetHandle}`
      const source = `${connection.source}.${connection.sourceHandle}`
      onChange({ ...spec, edges: [...spec.edges.filter(edge => edge.target !== target), { source, target }], nodes: spec.nodes.map(node => node.key === connection.target ? { ...node, inputs: { ...record(node.inputs), [connection.targetHandle!]: { from: source } } } : node) })
      setMessage('已连接。保存草稿后依赖生效。')
    }} onEdgesDelete={deleted => { if (!readOnly) onChange(removeStudyEdges(spec, deleted.map(edge => Number(edge.id.slice(5))))) }} deleteKeyCode={readOnly ? null : ['Backspace', 'Delete']}>
      <Background gap={20} size={1} /><Controls showInteractive={false} />
    </ReactFlow>
    <div className="pointer-events-none absolute left-3 top-3 rounded border bg-white/95 px-3 py-2 text-xs text-slate-500">{readOnly ? '已发布模型 · 拖动模块排列，滚轮缩放' : '拖动模块排列 · 从输出圆点拖到输入圆点连线 · 选中连线按 Delete 删除'}</div>
    {!nodes.length && <p className="pointer-events-none absolute inset-0 flex items-center justify-center text-sm text-slate-500">添加计算节点，开始构建模型</p>}
    {message && <p role="status" className="absolute bottom-3 right-3 bg-white px-3 py-2 text-xs">{message}</p>}
  </div>
}
