/* eslint-disable @typescript-eslint/no-explicit-any */
import { useMemo, useState } from "react";
import { AlertTriangle, Check, Loader2, Sparkles, Trash2 } from "lucide-react";
import { useQuery } from "@tanstack/react-query";
import { apiClientV2 } from "@/api/client";

type EditorEntity = {
  id: string;
  name_cn?: string;
  name_en?: string;
  canonical_id?: string;
  description?: string;
  property_definitions?: Array<Record<string, any>>;
};
type EditorRelation = {
  id: string;
  source_entity: string;
  target_entity: string;
  type: string;
  properties?: Record<string, any>;
};
type EditorRule = { id: string; name_cn: string; description?: string; condition?: any; effect?: any };
type Suggestion = { target_kind: Kind; operation: Operation; target_id?: string; payload?: Record<string, any>; reason?: string };
type BatchValidation = { can_apply?: boolean; message?: string; operations?: Array<Record<string, any>>; validation?: Record<string, any>; blockers?: Array<Record<string, any>>; warnings?: Array<Record<string, any>> };
type EditorData = {
  current_revision_id?: string | null;
  entities: EditorEntity[];
  relationships: EditorRelation[];
  logic_rules: EditorRule[];
  capabilities?: { cardinalities?: string[] };
};
type Kind = "entity_type" | "property" | "relationship" | "logic_rule";
type Operation = "add" | "update" | "delete";

const inputClass = "w-full rounded-md border border-slate-300 bg-white px-2.5 py-2 text-xs outline-none focus:border-slate-700";
const buttonClass = "rounded-md border border-slate-300 px-3 py-2 text-xs text-slate-700 hover:border-slate-600 disabled:opacity-40";

export default function OntologyEditorPanel({ ontologyId, onChanged }: { ontologyId: string; onChanged?: () => void }) {
  const [kind, setKind] = useState<Kind>("entity_type");
  const [operation, setOperation] = useState<Operation>("add");
  const [targetId, setTargetId] = useState("");
  const [entityId, setEntityId] = useState("");
  const [form, setForm] = useState<Record<string, any>>({});
  const [status, setStatus] = useState("");
  const [busy, setBusy] = useState(false);
  const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
  const [selectedSuggestions, setSelectedSuggestions] = useState<number[]>([]);
  const [batchOperations, setBatchOperations] = useState<Suggestion[]>([]);
  const [batchValidation, setBatchValidation] = useState<BatchValidation | null>(null);
  const { data, refetch } = useQuery({
    queryKey: ["ontology-editor", ontologyId],
    queryFn: () => apiClientV2.get<EditorData>(`/ontologies/${ontologyId}/editor`),
  });
  const entities = useMemo(() => data?.entities || [], [data?.entities]);
  const relations = useMemo(() => data?.relationships || [], [data?.relationships]);
  const rules = useMemo(() => data?.logic_rules || [], [data?.logic_rules]);
  const selectedEntity = useMemo(() => entities.find((item) => item.id === (entityId || targetId)), [entities, entityId, targetId]);
  const set = (key: string, value: any) => setForm((current) => ({ ...current, [key]: value }));
  const selectTarget = (value: string) => {
    setTargetId(value);
    const source: any = kind === "entity_type" ? entities.find((item) => item.id === value) : kind === "property" ? selectedEntity?.property_definitions?.find((item) => String(item.id || item.name) === value) : kind === "relationship" ? relations.find((item) => item.id === value) : rules.find((item) => item.id === value);
    if (source) setForm({ ...source, ...(kind === "relationship" ? { cardinality: source.properties?.cardinality || "one-to-many" } : {}) });
  };
  const changeKind = (value: Kind) => { setKind(value); setTargetId(""); setForm({}); if (value !== "property") setEntityId(""); };
  const deleteTarget = operation === "delete";
  const submit = async () => {
    if (deleteTarget && !targetId) return;
    setBusy(true); setStatus("");
    try {
      const payload: Record<string, any> = { ...form };
      // Selecting an existing entity hydrates the form with its property
      // definitions for display/validation.  Entity edits are intentionally
      // limited to the entity fields; property add/remove/update must remain
      // separate typed operations so one save cannot silently change a list.
      if (kind === "entity_type" && operation !== "add") {
        delete payload.properties;
        delete payload.property_definitions;
      }
      if (kind === "property") {
        payload.entity_id = entityId;
        if (operation === "add" || operation === "update") payload.property = undefined;
      }
      if (kind === "relationship" && payload.cardinality) {
        payload.properties = { ...(payload.properties || {}), cardinality: payload.cardinality };
        delete payload.cardinality;
      }
      if (kind === "logic_rule") {
        payload.condition = payload.condition || { all: [
          { kind: "relationship", predicate: payload.first_predicate || "IN_PHASE", source: "?observation", target: "?phase" },
          { kind: "relationship", predicate: payload.second_predicate || "HAS_TOOL_CONDITION", source: "?observation", target: "?condition" },
        ] };
        payload.effect = payload.effect || { kind: "relationship", predicate: payload.effect_predicate || "PHASE_TOOL_STATE", source: "?phase", target: "?condition" };
      }
      if (deleteTarget) {
        const impact = await apiClientV2.post<any>(`/ontologies/${ontologyId}/changes/impact`, { base_revision_id: data?.current_revision_id, target_kind: kind, operation, target_id: targetId, payload });
        if (!impact.can_apply) {
          setStatus(`删除被阻断：${(impact.blockers || []).map((item: any) => item.message).join("；")}`);
          return;
        }
      }
      await apiClientV2.post(`/ontologies/${ontologyId}/changes`, { base_revision_id: data?.current_revision_id, target_kind: kind, operation, target_id: targetId || undefined, payload });
      setStatus("已保存为新修订"); setForm({}); setTargetId(""); await refetch(); onChanged?.();
    } catch (error: any) {
      const detail = error?.detail || error?.response?.data?.detail;
      setStatus(typeof detail === "object" ? detail.message || "保存失败" : String(detail || error?.message || "保存失败"));
    } finally { setBusy(false); }
  };
  const requestSuggestion = async () => {
    setBusy(true); setStatus("");
    try { const result = await apiClientV2.post<any>(`/ontologies/${ontologyId}/model-suggestions`, { request: true }); setSuggestions(result.suggestions || []); setSelectedSuggestions([]); setBatchOperations([]); setBatchValidation(null); setStatus(result.message || "建议仅作为待填写草稿返回"); }
    catch (error: any) { setStatus(String(error?.message || "模型建议暂不可用")); }
    finally { setBusy(false); }
  };
  const fillSuggestion = (suggestion: Suggestion) => {
    setKind(suggestion.target_kind);
    setOperation(suggestion.operation);
    setTargetId(suggestion.target_id || "");
    const next = { ...(suggestion.payload || {}) };
    if (suggestion.target_kind === "property" && next.entity_id) setEntityId(String(next.entity_id));
    if (suggestion.target_kind === "relationship" && next.properties?.cardinality) next.cardinality = next.properties.cardinality;
    setForm(next);
    setStatus("已填入编辑表单，请检查后手动保存");
  };
  const fillSelectedSuggestions = () => {
    const selected = suggestions.filter((_item, index) => selectedSuggestions.includes(index));
    if (!selected.length) {
      setStatus("请先勾选要导入的模型建议");
      return;
    }
    setBatchOperations(selected.map((item) => ({ ...item, target_id: item.target_id || "", payload: JSON.parse(JSON.stringify(item.payload || {})) })));
    setBatchValidation(null);
    setStatus(`已将 ${selected.length} 条建议填入批量表单，请检查后进行冲突校验`);
  };
  const updateBatchOperation = (index: number, key: string, value: any) => {
    setBatchOperations((current) => current.map((item, itemIndex) => {
      if (itemIndex !== index) return item;
      if (key === "target_id") {
        const next = { ...item, target_id: value };
        if (item.target_kind === "property" && item.operation === "add") {
          next.payload = { ...(item.payload || {}), property: { ...(item.payload?.property || {}), id: value } };
        }
        return next;
      }
      const payload = { ...(item.payload || {}) };
      if (item.target_kind === "property" && !["entity_id"].includes(key)) {
        payload.property = { ...(payload.property || {}), [key]: value };
      } else if (item.target_kind === "relationship" && key === "cardinality") {
        payload.properties = { ...(payload.properties || {}), cardinality: value };
      } else {
        payload[key] = value;
      }
      return { ...item, payload };
    }));
    setBatchValidation(null);
  };
  const batchField = (item: Suggestion, key: string) => {
    const payload = item.payload || {};
    if (item.target_kind === "property" && key !== "entity_id") return payload.property?.[key] ?? payload[key] ?? "";
    if (item.target_kind === "relationship" && key === "cardinality") return payload.properties?.cardinality ?? payload.cardinality ?? "one-to-many";
    return payload[key] ?? "";
  };
  const batchBody = () => ({
    base_revision_id: data?.current_revision_id,
    operations: batchOperations.map((item) => ({ target_kind: item.target_kind, operation: item.operation, target_id: item.target_id || undefined, payload: item.payload || {} })),
  });
  const validateBatch = async () => {
    if (!batchOperations.length) return;
    setBusy(true); setStatus("");
    try {
      const result = await apiClientV2.post<BatchValidation>(`/ontologies/${ontologyId}/changes/batch/validate`, batchBody());
      setBatchValidation(result);
      setStatus(result.message || "批量建议已通过整体校验");
    } catch (error: any) {
      const detail = error?.detail || error?.response?.data?.detail;
      const detailObject = typeof detail === "object" && detail !== null ? detail : {};
      const blockers = [
        ...(Array.isArray(detailObject.blockers) ? detailObject.blockers : []),
        ...(Array.isArray(detailObject.relations) ? detailObject.relations.map((item: any) => ({ message: `关系 ${item.id || "未命名"}：${item.reason || "结构无效"}` })) : []),
        ...(Array.isArray(detailObject.rules) ? detailObject.rules.map((item: any) => ({ message: `规则 ${item.id || "未命名"}：${item.reason || "引用无效"}` })) : []),
      ];
      setBatchValidation({ can_apply: false, message: typeof detail === "object" ? detail.message : String(detail || "批量校验失败"), blockers });
      setStatus(typeof detail === "object" ? detail.message || "批量校验失败" : String(detail || error?.message || "批量校验失败"));
    } finally { setBusy(false); }
  };
  const applyBatch = async () => {
    if (!batchOperations.length || !batchValidation?.can_apply) {
      setStatus("请先完成批量冲突校验");
      return;
    }
    setBusy(true); setStatus("");
    try {
      const result = await apiClientV2.post<any>(`/ontologies/${ontologyId}/changes/batch`, batchBody());
      setStatus(`已批量导入 ${batchOperations.length} 条建议，生成新修订 ${result.revision?.revision_no ?? ""}`);
      setSuggestions((current) => current.filter((_item, index) => !selectedSuggestions.includes(index)));
      setSelectedSuggestions([]); setBatchOperations([]); setBatchValidation(null);
      await refetch(); onChanged?.();
    } catch (error: any) {
      const detail = error?.detail || error?.response?.data?.detail;
      setStatus(typeof detail === "object" ? detail.message || "批量导入失败" : String(detail || error?.message || "批量导入失败"));
    } finally { setBusy(false); }
  };
  if (!data) return <div className="mb-4 rounded-xl border border-slate-200 bg-white p-4 text-xs text-slate-500"><Loader2 size={14} className="mr-2 inline animate-spin" />正在读取编辑器</div>;
  return <section className="mb-4 rounded-xl border border-slate-300 bg-slate-50 p-4" data-testid="ontology-editor">
    <div className="mb-3 flex flex-wrap items-center justify-between gap-2"><div><h2 className="text-sm font-semibold text-slate-900">编辑本体</h2><p className="mt-1 text-[11px] text-slate-500">每次保存一个修改，自动生成新修订；真实实例保持只读。</p></div><button onClick={requestSuggestion} disabled={busy} className="inline-flex items-center gap-1 rounded-md border border-slate-300 bg-white px-3 py-2 text-xs text-slate-700"><Sparkles size={13} />模型建议</button></div>
    <div className="grid gap-3 xl:grid-cols-[150px_120px_minmax(0,1fr)]">
      <select aria-label="修改对象" value={kind} onChange={(event) => changeKind(event.target.value as Kind)} className={inputClass}><option value="entity_type">实体类型</option><option value="property">属性</option><option value="relationship">类型关系</option><option value="logic_rule">逻辑规则</option></select>
      <select aria-label="修改操作" value={operation} onChange={(event) => { setOperation(event.target.value as Operation); setTargetId(""); setForm({}); }} className={inputClass}><option value="add">新增</option><option value="update">修改</option><option value="delete">删除</option></select>
      {operation !== "add" && <select aria-label="选择对象" value={targetId} onChange={(event) => selectTarget(event.target.value)} className={inputClass}><option value="">选择已有对象</option>{kind === "entity_type" && entities.map((item) => <option key={item.id} value={item.id}>{item.name_cn || item.name_en || item.id}</option>)}{kind === "property" && selectedEntity?.property_definitions?.map((item) => <option key={String(item.id || item.name)} value={String(item.id || item.name)}>{String(item.label || item.name || item.id)}</option>)}{kind === "relationship" && relations.map((item) => <option key={item.id} value={item.id}>{item.type}</option>)}{kind === "logic_rule" && rules.map((item) => <option key={item.id} value={item.id}>{item.name_cn}</option>)}</select>}
    </div>
    <div className="mt-3 grid gap-3 md:grid-cols-2">
      {kind === "entity_type" && <><label className="text-xs text-slate-600">中文名称<input className={inputClass} value={form.name_cn || ""} onChange={(event) => set("name_cn", event.target.value)} placeholder="例如：设备" /></label><label className="text-xs text-slate-600">英文名称<input className={inputClass} value={form.name_en || ""} onChange={(event) => set("name_en", event.target.value)} placeholder="Equipment" /></label><label className="text-xs text-slate-600 md:col-span-2">说明<textarea className={inputClass} value={form.description || ""} onChange={(event) => set("description", event.target.value)} rows={2} /></label></>}
      {kind === "property" && <><label className="text-xs text-slate-600">所属实体类型<select className={inputClass} value={entityId} onChange={(event) => setEntityId(event.target.value)}><option value="">选择实体类型</option>{entities.map((item) => <option key={item.id} value={item.id}>{item.name_cn || item.name_en || item.id}</option>)}</select></label><label className="text-xs text-slate-600">属性标识<input className={inputClass} value={targetId || form.id || form.name || ""} disabled={operation === "update" || operation === "delete"} onChange={(event) => { setTargetId(event.target.value); set("id", event.target.value); }} placeholder="例如：cycle" /></label><label className="text-xs text-slate-600">显示名称<input className={inputClass} value={form.label || form.name || ""} onChange={(event) => { set("label", event.target.value); set("name", event.target.value); }} /></label><label className="text-xs text-slate-600">数据类型<select className={inputClass} value={form.type || "string"} onChange={(event) => set("type", event.target.value)}><option>string</option><option>integer</option><option>decimal</option><option>boolean</option></select></label><label className="text-xs text-slate-600 md:col-span-2">单位<input className={inputClass} value={form.unit || ""} onChange={(event) => set("unit", event.target.value)} placeholder="可选" /></label></>}
      {kind === "relationship" && <><label className="text-xs text-slate-600">起点实体类型<select className={inputClass} value={form.source_entity || ""} onChange={(event) => set("source_entity", event.target.value)}><option value="">选择</option>{entities.map((item) => <option key={item.id} value={item.id}>{item.name_cn || item.name_en || item.id}</option>)}</select></label><label className="text-xs text-slate-600">终点实体类型<select className={inputClass} value={form.target_entity || ""} onChange={(event) => set("target_entity", event.target.value)}><option value="">选择</option>{entities.map((item) => <option key={item.id} value={item.id}>{item.name_cn || item.name_en || item.id}</option>)}</select></label><label className="text-xs text-slate-600">关系名称<input className={inputClass} value={form.type || ""} onChange={(event) => set("type", event.target.value)} placeholder="例如：拥有观测" /></label><label className="text-xs text-slate-600">基数<select className={inputClass} value={form.cardinality || "one-to-many"} onChange={(event) => set("cardinality", event.target.value)}>{(data.capabilities?.cardinalities || ["one-to-one", "one-to-many", "many-to-one", "many-to-many"]).map((value) => <option key={value}>{value}</option>)}</select></label></>}
      {kind === "logic_rule" && <><label className="text-xs text-slate-600">规则名称<input className={inputClass} value={form.name_cn || ""} onChange={(event) => set("name_cn", event.target.value)} placeholder="例如：阶段刀具状态" /></label><label className="text-xs text-slate-600">关联实体 ID（逗号分隔）<input className={inputClass} value={(form.linked_entities || []).join(",")} onChange={(event) => set("linked_entities", event.target.value.split(",").map((item) => item.trim()).filter(Boolean))} /></label><label className="text-xs text-slate-600 md:col-span-2">关系前提（用两个可视化字段表达）<div className="grid gap-2 md:grid-cols-2"><input className={inputClass} value={form.first_predicate || "IN_PHASE"} onChange={(event) => set("first_predicate", event.target.value)} placeholder="第一个关系" /><input className={inputClass} value={form.second_predicate || "HAS_TOOL_CONDITION"} onChange={(event) => set("second_predicate", event.target.value)} placeholder="第二个关系" /></div></label><label className="text-xs text-slate-600">结论关系<input className={inputClass} value={form.effect_predicate || "PHASE_TOOL_STATE"} onChange={(event) => set("effect_predicate", event.target.value)} /></label></>}
    </div>
    {kind === "logic_rule" && <p className="mt-2 text-[11px] text-slate-500">规则保存为结构化 IF/THEN；页面不接受原始规则代码。</p>}
    {status && <div className="mt-3 flex items-start gap-2 rounded-md border border-slate-200 bg-white px-3 py-2 text-xs text-slate-700">{status.includes("阻断") ? <AlertTriangle size={14} className="mt-0.5 shrink-0" /> : <Check size={14} className="mt-0.5 shrink-0" />}{status}</div>}
    {suggestions.length > 0 && <div className="mt-3 rounded-md border border-slate-200 bg-white p-3" data-testid="model-suggestions"><div className="mb-2 flex flex-wrap items-center justify-between gap-2"><div className="flex items-center gap-2 text-xs font-semibold text-slate-700"><input aria-label="全选模型建议" type="checkbox" checked={selectedSuggestions.length === suggestions.length && suggestions.length > 0} onChange={(event) => setSelectedSuggestions(event.target.checked ? suggestions.map((_item, index) => index) : [])} />待确认模型建议 <span className="font-normal text-slate-400">已选 {selectedSuggestions.length} / {suggestions.length}</span></div><button onClick={fillSelectedSuggestions} disabled={busy || selectedSuggestions.length === 0} className="rounded border border-slate-300 px-2.5 py-1.5 text-[11px] text-slate-700 hover:border-slate-600 disabled:opacity-40">批量填入表单</button></div><div className="space-y-2">{suggestions.map((suggestion, index) => <div key={`${suggestion.target_kind}-${suggestion.operation}-${index}`} className={`flex items-start gap-2 rounded border px-2.5 py-2 text-[11px] ${selectedSuggestions.includes(index) ? "border-slate-400 bg-slate-50" : "border-slate-100 bg-white"}`}><input aria-label={`选择第 ${index + 1} 条模型建议`} type="checkbox" className="mt-0.5" checked={selectedSuggestions.includes(index)} onChange={(event) => setSelectedSuggestions((current) => event.target.checked ? [...new Set([...current, index])] : current.filter((value) => value !== index))} /><div className="min-w-0 flex-1"><span className="font-medium text-slate-700">{suggestion.operation === "add" ? "新增" : suggestion.operation === "update" ? "修改" : "删除"} · {suggestion.target_kind}</span>{suggestion.target_id && <span className="ml-2 font-mono text-slate-500">{suggestion.target_id}</span>}{suggestion.reason && <p className="mt-1 text-slate-500">{suggestion.reason}</p>}</div><button onClick={() => fillSuggestion(suggestion)} className="shrink-0 rounded border border-slate-300 px-2 py-1 text-[11px] text-slate-700 hover:border-slate-600">单项填入</button></div>)}</div></div>}
    {batchOperations.length > 0 && <div className="mt-3 rounded-md border border-slate-300 bg-white p-3" data-testid="batch-suggestion-form"><div className="flex flex-wrap items-center justify-between gap-2"><div><h3 className="text-xs font-semibold text-slate-800">批量填入表单</h3><p className="mt-1 text-[11px] text-slate-500">下列修改会作为一个新修订整体提交；请先检查冲突。</p></div><button onClick={() => { setBatchOperations([]); setBatchValidation(null); }} className="text-[11px] text-slate-500 hover:text-slate-800">清空批量表单</button></div><div className="mt-3 space-y-3">{batchOperations.map((item, index) => <div key={`batch-${item.target_kind}-${item.target_id}-${index}`} className="rounded-lg border border-slate-200 bg-slate-50 p-3"><div className="flex items-center justify-between gap-2"><span className="text-[11px] font-semibold text-slate-700">{index + 1}. {item.operation === "add" ? "新增" : item.operation === "update" ? "修改" : "删除"} · {item.target_kind}</span><button onClick={() => { setBatchOperations((current) => current.filter((_entry, entryIndex) => entryIndex !== index)); setBatchValidation(null); }} className="text-[11px] text-slate-400 hover:text-red-600">移除</button></div>{item.operation === "delete" ? <p className="mt-2 text-[11px] text-slate-500">目标：<span className="font-mono">{item.target_id || "未指定"}</span>；导入前会检查实例、关系、规则和证据依赖。</p> : <div className="mt-2 grid gap-2 md:grid-cols-2">{item.target_kind === "entity_type" && <><label className="text-[11px] text-slate-600">实体标识<input className={inputClass} value={String(batchField(item, "canonical_id") || "")} disabled={item.operation !== "add"} onChange={(event) => updateBatchOperation(index, "canonical_id", event.target.value)} /></label><label className="text-[11px] text-slate-600">中文名称<input className={inputClass} value={String(batchField(item, "name_cn") || "")} onChange={(event) => updateBatchOperation(index, "name_cn", event.target.value)} /></label><label className="text-[11px] text-slate-600">英文名称<input className={inputClass} value={String(batchField(item, "name_en") || "")} onChange={(event) => updateBatchOperation(index, "name_en", event.target.value)} /></label><label className="text-[11px] text-slate-600 md:col-span-2">说明<textarea className={inputClass} rows={2} value={String(batchField(item, "description") || "")} onChange={(event) => updateBatchOperation(index, "description", event.target.value)} /></label></>}{item.target_kind === "property" && <><label className="text-[11px] text-slate-600">所属实体类型<select className={inputClass} value={String(batchField(item, "entity_id") || "")} onChange={(event) => updateBatchOperation(index, "entity_id", event.target.value)}><option value="">选择实体类型</option>{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name_cn || entity.name_en || entity.id}</option>)}</select></label><label className="text-[11px] text-slate-600">属性标识<input className={inputClass} value={String(item.target_id || batchField(item, "id") || "")} disabled={item.operation !== "add"} onChange={(event) => updateBatchOperation(index, "target_id", event.target.value)} /></label><label className="text-[11px] text-slate-600">显示名称<input className={inputClass} value={String(batchField(item, "label") || batchField(item, "name") || "")} onChange={(event) => updateBatchOperation(index, "label", event.target.value)} /></label><label className="text-[11px] text-slate-600">数据类型<select className={inputClass} value={String(batchField(item, "type") || "string")} onChange={(event) => updateBatchOperation(index, "type", event.target.value)}><option>string</option><option>integer</option><option>decimal</option><option>boolean</option></select></label></>}{item.target_kind === "relationship" && <><label className="text-[11px] text-slate-600">起点实体类型<select className={inputClass} value={String(batchField(item, "source_entity") || "")} onChange={(event) => updateBatchOperation(index, "source_entity", event.target.value)}><option value="">选择起点</option>{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name_cn || entity.name_en || entity.id}</option>)}</select></label><label className="text-[11px] text-slate-600">终点实体类型<select className={inputClass} value={String(batchField(item, "target_entity") || "")} onChange={(event) => updateBatchOperation(index, "target_entity", event.target.value)}><option value="">选择终点</option>{entities.map((entity) => <option key={entity.id} value={entity.id}>{entity.name_cn || entity.name_en || entity.id}</option>)}</select></label><label className="text-[11px] text-slate-600">关系名称<input className={inputClass} value={String(batchField(item, "type") || "")} onChange={(event) => updateBatchOperation(index, "type", event.target.value)} /></label><label className="text-[11px] text-slate-600">基数<select className={inputClass} value={String(batchField(item, "cardinality") || "one-to-many")} onChange={(event) => updateBatchOperation(index, "cardinality", event.target.value)}>{(data.capabilities?.cardinalities || ["one-to-one", "one-to-many", "many-to-one", "many-to-many"]).map((value) => <option key={value}>{value}</option>)}</select></label></>}{item.target_kind === "logic_rule" && <><label className="text-[11px] text-slate-600">规则名称<input className={inputClass} value={String(batchField(item, "name_cn") || "")} onChange={(event) => updateBatchOperation(index, "name_cn", event.target.value)} /></label><label className="text-[11px] text-slate-600">说明<textarea className={inputClass} rows={2} value={String(batchField(item, "description") || "")} onChange={(event) => updateBatchOperation(index, "description", event.target.value)} /></label></>}</div>}</div>)}</div>{batchValidation && <div className={`mt-3 rounded-md border px-3 py-2 text-[11px] ${batchValidation.can_apply ? "border-emerald-200 bg-emerald-50 text-emerald-800" : "border-red-200 bg-red-50 text-red-800"}`}><p className="font-medium">{batchValidation.can_apply ? "整体校验通过" : "批量导入存在冲突"}</p>{batchValidation.message && <p className="mt-1">{batchValidation.message}</p>}{(batchValidation.blockers || []).map((item, index) => <p key={`blocker-${index}`} className="mt-1">阻断：{String(item.message || item.reason || item.code || "未通过")}</p>)}{(batchValidation.warnings || []).map((item, index) => <p key={`warning-${index}`} className="mt-1">提示：{String(item.message || item.reason || "请确认")}</p>)}</div>}<div className="mt-3 flex flex-wrap justify-end gap-2"><button onClick={() => void validateBatch()} disabled={busy} className={buttonClass}>{busy && <Loader2 size={13} className="mr-1 inline animate-spin" />}检查冲突</button><button onClick={() => void applyBatch()} disabled={busy || !batchValidation?.can_apply} className="rounded-md bg-slate-800 px-3 py-2 text-xs text-white disabled:opacity-40">确认批量导入</button></div></div>}
    <div className="mt-3 flex items-center justify-end gap-2"><button onClick={() => { setForm({}); setTargetId(""); }} className={buttonClass}>清空</button><button onClick={submit} disabled={busy || (operation !== "add" && !targetId) || (kind === "property" && !entityId)} className="inline-flex items-center gap-1 rounded-md bg-slate-800 px-4 py-2 text-xs text-white hover:bg-slate-700 disabled:opacity-40">{busy && <Loader2 size={13} className="animate-spin" />}{operation === "delete" ? <Trash2 size={13} /> : null}{operation === "delete" ? "删除" : "保存修改"}</button></div>
  </section>;
}
