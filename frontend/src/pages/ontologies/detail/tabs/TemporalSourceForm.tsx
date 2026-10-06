import { useState, type FormEvent } from "react";

export type TemporalSourcePayload = {
  source_id: string;
  source_mode: "push" | "file_replay";
  dataset_id?: string;
  dataset_version_id?: string;
  time_column: string;
  entity_column: string;
  config: {
    time_kind: "ordinal" | "event_time";
    late_event_policy: "reject";
    mapping: {
      entity_type: string;
      entity_key_field?: string;
      properties: Record<string, string>;
    };
  };
};

export default function TemporalSourceForm({
  busy,
  onCreate,
}: {
  busy: boolean;
  onCreate: (payload: TemporalSourcePayload) => Promise<void>;
}) {
  const [mode, setMode] = useState<"push" | "file_replay">("push");
  const [sourceId, setSourceId] = useState("");
  const [datasetId, setDatasetId] = useState("");
  const [datasetVersionId, setDatasetVersionId] = useState("");
  const [entityType, setEntityType] = useState("");
  const [entityKeyField, setEntityKeyField] = useState("entity_id");
  const [entityColumn, setEntityColumn] = useState("episode_id");
  const [timeKind, setTimeKind] = useState<"ordinal" | "event_time">("event_time");
  const [timeColumn, setTimeColumn] = useState("event_time");
  const [properties, setProperties] = useState('{"temperature":"temperature"}');
  const [error, setError] = useState("");

  const submit = async (event: FormEvent) => {
    event.preventDefault();
    setError("");
    let parsed: unknown;
    try {
      parsed = JSON.parse(properties);
    } catch {
      setError("属性映射必须是 JSON 对象，例如 {\"源字段\":\"本体属性\"}。");
      return;
    }
    if (!parsed || Array.isArray(parsed) || typeof parsed !== "object" || !Object.keys(parsed).length) {
      setError("至少声明一个源字段到本体属性的映射。");
      return;
    }
    await onCreate({
      source_id: sourceId.trim(),
      source_mode: mode,
      dataset_id: mode === "file_replay" ? datasetId.trim() : undefined,
      dataset_version_id: mode === "file_replay" && datasetVersionId.trim() ? datasetVersionId.trim() : undefined,
      time_column: timeColumn.trim(),
      entity_column: entityColumn.trim(),
      config: {
        time_kind: timeKind,
        late_event_policy: "reject",
        mapping: {
          entity_type: entityType.trim(),
          entity_key_field: mode === "file_replay" ? entityKeyField.trim() : undefined,
          properties: parsed as Record<string, string>,
        },
      },
    });
  };

  const input = "rounded border border-slate-300 bg-white px-2.5 py-2 text-xs";
  return <details className="rounded-xl border border-slate-200 bg-white p-5">
    <summary className="cursor-pointer text-sm font-medium text-slate-900">连接通用时序来源</summary>
    <p className="mt-2 text-xs text-slate-500">来源必须映射到当前固定 schema revision；当前迟到事件策略为拒绝并返回稳定错误。</p>
    <form className="mt-4 grid gap-3 md:grid-cols-2" onSubmit={(event) => void submit(event)}>
      <label className="grid gap-1 text-xs text-slate-600">来源 ID<input required maxLength={120} className={input} value={sourceId} onChange={(event) => setSourceId(event.target.value)} placeholder="plant_sensor_gateway" /></label>
      <label className="grid gap-1 text-xs text-slate-600">来源模式<select className={input} value={mode} onChange={(event) => setMode(event.target.value as "push" | "file_replay")}><option value="push">Push</option><option value="file_replay">文件回放</option></select></label>
      {mode === "file_replay" && <>
        <label className="grid gap-1 text-xs text-slate-600">Dataset ID<input required className={input} value={datasetId} onChange={(event) => setDatasetId(event.target.value)} /></label>
        <label className="grid gap-1 text-xs text-slate-600">Dataset Version ID（可选）<input className={input} value={datasetVersionId} onChange={(event) => setDatasetVersionId(event.target.value)} /></label>
      </>}
      <label className="grid gap-1 text-xs text-slate-600">本体对象类型<input required className={input} value={entityType} onChange={(event) => setEntityType(event.target.value)} placeholder="Machine" /></label>
      {mode === "file_replay" && <label className="grid gap-1 text-xs text-slate-600">对象主键字段<input required className={input} value={entityKeyField} onChange={(event) => setEntityKeyField(event.target.value)} /></label>}
      <label className="grid gap-1 text-xs text-slate-600">时间语义<select className={input} value={timeKind} onChange={(event) => { const value = event.target.value as "ordinal" | "event_time"; setTimeKind(value); setTimeColumn(value === "event_time" ? "event_time" : "ordinal"); }}><option value="event_time">Event time（带时区）</option><option value="ordinal">Ordinal</option></select></label>
      <label className="grid gap-1 text-xs text-slate-600">时间字段<input required className={input} value={timeColumn} onChange={(event) => setTimeColumn(event.target.value)} /></label>
      <label className="grid gap-1 text-xs text-slate-600">分组字段<input required className={input} value={entityColumn} onChange={(event) => setEntityColumn(event.target.value)} /></label>
      <label className="grid gap-1 text-xs text-slate-600 md:col-span-2">属性映射 JSON<textarea required rows={3} className={`${input} font-mono`} value={properties} onChange={(event) => setProperties(event.target.value)} /></label>
      {error && <p role="alert" className="text-xs text-red-700 md:col-span-2">{error}</p>}
      <div className="flex items-center justify-between gap-3 md:col-span-2"><span className="text-[11px] text-slate-500">late_event_policy: reject</span><button disabled={busy} className="rounded bg-slate-900 px-3 py-2 text-xs text-white disabled:opacity-50">{busy ? "创建中" : "创建来源运行"}</button></div>
    </form>
  </details>;
}
