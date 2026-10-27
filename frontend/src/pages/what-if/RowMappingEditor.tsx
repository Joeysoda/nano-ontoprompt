type Row = Record<string, unknown>
const record = (value: unknown): Row => value && typeof value === 'object' && !Array.isArray(value) ? value as Row : {}
const text = (value: unknown) => value == null ? '' : String(value)

export default function RowMappingEditor({ config, onChange }: { config: Row; onChange: (config: Row) => void }) {
  const fields = Array.isArray(config.fields) ? config.fields.map(record) : []
  const change = (index: number, patch: Row) => onChange({ ...config, fields: fields.map((field, position) => position === index ? { ...field, ...patch } : field) })
  return <section className="mt-3 space-y-3" aria-label="字段映射配置">
    <p className="text-xs leading-5 text-slate-500">连接查询输出到 rows，再连接映射的 rows 到函数输入。来源路径例如 properties.quantity；单位两端都填写，留空表示不转换。缺少必填字段会停止运行。</p>
    {fields.map((field, index) => <fieldset key={index} className="space-y-2 rounded border p-2">
      <legend className="text-xs">映射 {index + 1}</legend>
      <label className="block text-xs">目标字段<input className="wb-input mt-1 w-full" value={text(field.target)} onChange={event => change(index, { target: event.target.value })} /></label>
      <label className="block text-xs">来源路径<input className="wb-input mt-1 w-full" value={Array.isArray(field.source) ? field.source.join('.') : ''} onChange={event => change(index, { source: event.target.value.split('.') })} /></label>
      <label className="block text-xs">字段类型<select className="wb-input mt-1 w-full" value={text(field.data_type || 'number')} onChange={event => change(index, { data_type: event.target.value })}>{['string', 'number', 'integer', 'boolean', 'array', 'object'].map(type => <option key={type}>{type}</option>)}</select></label>
      <div className="flex gap-3 text-xs"><label><input type="checkbox" checked={field.required !== false} onChange={event => change(index, { required: event.target.checked })} /> 必填</label><label><input type="checkbox" checked={field.nullable === true} onChange={event => change(index, { nullable: event.target.checked })} /> 允许空值</label></div>
      <label className="block text-xs">来源单位<input className="wb-input mt-1 w-full" value={text(field.source_unit)} placeholder="例如 min" onChange={event => change(index, { source_unit: event.target.value || null })} /></label>
      <label className="block text-xs">目标单位<input className="wb-input mt-1 w-full" value={text(field.target_unit)} placeholder="例如 s" onChange={event => change(index, { target_unit: event.target.value || null })} /></label>
      <button className="text-xs text-rose-700" onClick={() => onChange({ ...config, fields: fields.filter((_, position) => position !== index) })}>删除映射</button>
    </fieldset>)}
    <button className="wb-button-secondary" onClick={() => onChange({ ...config, fields: [...fields, { target: '', source: ['properties', ''], data_type: 'number', required: true, nullable: false }] })}>＋ 添加字段映射</button>
  </section>
}
