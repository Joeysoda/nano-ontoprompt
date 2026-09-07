import { useEffect, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import {
  ArrowLeft,
  Check,
  FileUp,
  Loader2,
  Play,
  RefreshCw,
  ShieldCheck,
  TriangleAlert,
} from "lucide-react";
import { apiClient, apiClientV2 } from "@/api/client";

type Source = {
  id: string;
  name: string;
  installed: boolean;
  dataset_id?: string;
  records?: number;
  columns?: Array<
    string | { name?: string; column?: string; type?: string; dtype?: string }
  >;
  source_url?: string;
  license?: string;
  filename?: string;
  sha256?: string;
  time_kind?: "instant" | "ordinal" | "interval";
  time_column?: string;
  entity_column?: string;
  related_dataset_id?: string;
  privacy_level?: "standard" | "private";
};
type Ontology = {
  id: string;
  name: string;
  domain?: string;
  data_class?: string;
};
type Profile = {
  id: string;
  status: string;
  model_name?: string;
  llm_used?: boolean;
  deterministic_profile?: any;
  llm_suggestion?: any;
  response_hash?: string;
  error?: string;
};
const steps = ["选择数据", "筛选数据", "时间定义", "本体映射", "确认构建"];
const fmt = (v: any) =>
  v === null || v === undefined || v === "" ? "—" : String(v);
const errorText = (e: any) =>
  e?.response?.data?.detail?.message ||
  e?.response?.data?.detail ||
  e?.detail?.message ||
  e?.detail ||
  e?.message ||
  "请求失败";
const columnName = (value: unknown) => {
  if (typeof value === "string") return value;
  if (value && typeof value === "object") {
    const item = value as { name?: string; column?: string };
    return item.name || item.column || "";
  }
  return "";
};
const defaultRole = (
  column: string,
  entityColumn: string,
  timeColumn: string,
  timeKind: string,
) =>
  column === entityColumn
    ? "实体标识"
    : column === timeColumn
      ? timeKind === "ordinal"
        ? "Ordinal 顺序"
        : timeKind === "instant"
          ? "Instant 时间点"
          : "Interval 边界"
      : "观测属性";

export default function TemporalConstructionWizard() {
  const navigate = useNavigate();
  const uploadRef = useRef<HTMLInputElement>(null);
  const [step, setStep] = useState(0);
  const [history, setHistory] = useState<any[]>([]);
  const [showHistory, setShowHistory] = useState(false);
  const [sources, setSources] = useState<Source[]>([]);
  const [source, setSource] = useState<Source | null>(null);
  const [profile, setProfile] = useState<Profile | null>(null);
  const [preview, setPreview] = useState<any>(null);
  const [ontologies, setOntologies] = useState<Ontology[]>([]);
  const [filters, setFilters] = useState<Record<string, any>>({
    max_records: 5000,
  });
  const [timeKind, setTimeKind] = useState<"instant" | "ordinal" | "interval">(
    "ordinal",
  );
  const [entityColumn, setEntityColumn] = useState("_series_id");
  const [timeColumn, setTimeColumn] = useState("_event_seq");
  const [fromColumn, setFromColumn] = useState("valid_from");
  const [toColumn, setToColumn] = useState("valid_to");
  const [ontologyMode, setOntologyMode] = useState<"create" | "reuse">(
    "create",
  );
  const [ontologyId, setOntologyId] = useState("");
  const [ontologyName, setOntologyName] = useState("FactoryNet CNC 时序本体");
  const [fieldMap, setFieldMap] = useState<
    Record<string, { target: string; role: string }>
  >({});
  const [privacy, setPrivacy] = useState<"standard" | "private">("standard");
  const [relations, setRelations] = useState<string[]>([
    "HAS_EPISODE",
    "HAS_OBSERVATION",
    "OBSERVED_ON",
    "NEXT_OBSERVATION",
    "IN_PHASE",
    "HAS_TOOL_CONDITION",
    "EXPOSES_CHANNEL",
  ]);
  const [loading, setLoading] = useState(true);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState("");
  const [updatedAt, setUpdatedAt] = useState("");
  const timeConfigRef = useRef("");
  const load = async () => {
    setLoading(true);
    setError("");
    try {
      const [ss, oo, rr] = await Promise.all([
        apiClientV2.get<Source[]>("/temporal/sources"),
        apiClient.get<any>("/ontologies", {
          params: { page: 1, page_size: 100 },
        }),
        apiClientV2.get<any[]>("/temporal/runs", { params: { limit: 20 } }),
      ]);
      setSources(
        (Array.isArray(ss) ? ss : []).filter(
          (item) => item.id !== "cmapss_fd004",
        ),
      );
      const ontologyRows = Array.isArray(oo) ? oo : oo?.items || [];
      setOntologies(
        ontologyRows.filter((item: Ontology) => item.data_class === "temporal"),
      );
      setHistory(Array.isArray(rr) ? rr : []);
      setUpdatedAt(new Date().toLocaleTimeString());
    } catch (e: any) {
      setError(errorText(e));
    } finally {
      setLoading(false);
    }
  };
  useEffect(() => {
    load();
  }, []);
  useEffect(() => {
    if (step === 1 && source?.installed) query(filters);
  }, [step, source?.id]);
  const pollProfile = (id: string) => {
    const timer = window.setInterval(async () => {
      try {
        const p = await apiClientV2.get<Profile>(`/temporal/analyses/${id}`);
        setProfile(p);
        if (p.status === "completed" || p.status === "failed") {
          window.clearInterval(timer);
          if (p.status === "failed") setError(p.error || "MiniMax 分析失败");
        }
      } catch (e: any) {
        window.clearInterval(timer);
        setError(errorText(e));
      }
    }, 1200);
  };
  const startAnalysis = async (item: Source) => {
    setSource(item);
    setPrivacy(item.privacy_level || "standard");
    setProfile(null);
    setPreview(null);
    setError("");
    const cols = (item.columns || []).map(columnName).filter(Boolean);
    const pick = (re: RegExp, f: string) => cols.find((c) => re.test(c)) || f;
    setEntityColumn(
      item.id === "factorynet_cnc"
        ? cols.includes("episode_id")
          ? "episode_id"
          : pick(/episode|series|machine|device|unit|entity|id/i, "_series_id")
        : item.id === "cmapss_fd004"
          ? item.entity_column ||
            (cols.includes("equipment_id") ? "equipment_id" : "_series_id")
          : pick(/episode|series|machine|device|unit|entity|id/i, "_series_id"),
    );
    setTimeColumn(
      item.id === "factorynet_cnc" && cols.includes("time_s")
        ? "time_s"
        : item.id === "cmapss_fd004" && cols.includes("cycle")
          ? "cycle"
          : pick(/time|timestamp|date|cycle|step|seq/i, "_event_seq"),
    );
    setTimeKind(
      item.id === "factorynet_cnc" || item.id === "cmapss_fd004"
        ? "ordinal"
        : item.time_kind || "instant",
    );
    if (!item.installed) return;
    try {
      setPreview(
        await apiClientV2.get(
          `/temporal/sources/${encodeURIComponent(item.id)}/preview`,
          { params: { offset: 0, limit: 25 } },
        ),
      );
    } catch (e: any) {
      setError(errorText(e));
    }
  };
  const processAnalysis = async () => {
    if (!source?.installed) {
      setError("请先选择已安装的数据源");
      return;
    }
    setBusy(true);
    setError("");
    try {
      const p = await apiClientV2.post<Profile>(
        `/temporal/sources/${encodeURIComponent(source.id)}/analyses`,
        { privacy_level: privacy },
      );
      setProfile(p);
      if (p.status === "failed") setError(p.error || "MiniMax 分析失败");
      else if (p.status === "queued" || p.status === "running")
        pollProfile(p.id);
    } catch (e: any) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const install = async () => {
    setBusy(true);
    setError("");
    try {
      await apiClientV2.post("/temporal/sources/factorynet_cnc/install");
      const ss = await apiClientV2.get<Source[]>("/temporal/sources");
      setSources(ss);
      const item = ss.find((x) => x.id === "factorynet_cnc");
      if (item) await startAnalysis(item);
    } catch (e: any) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const upload = async (file: File) => {
    setBusy(true);
    setError("");
    try {
      const form = new FormData();
      form.append("file", file);
      const r = await apiClientV2.post<any>("/temporal/sources/upload", form);
      const ss = await apiClientV2.get<Source[]>("/temporal/sources");
      setSources(ss);
      const item = ss.find((x) => x.id === r.source_id);
      if (item) await startAnalysis(item);
    } catch (e: any) {
      setError(errorText(e));
    } finally {
      setBusy(false);
      if (uploadRef.current) uploadRef.current.value = "";
    }
  };
  const query = async (next: Record<string, any>) => {
    if (!source?.installed) return;
    try {
      setPreview(
        await apiClientV2.post(
          `/temporal/sources/${encodeURIComponent(source.id)}/query`,
          {
            offset: 0,
            limit: 25,
            max_records: next.max_records,
            equals: next.equals || {},
            contains: next.contains || {},
            ranges: next.ranges || {},
            entity_column: entityColumn,
          },
        ),
      );
    } catch (e: any) {
      setError(errorText(e));
    }
  };
  const updateMax = (v: string) => {
    const next = { ...filters, max_records: v ? Number(v) : undefined };
    setFilters(next);
    query(next);
  };
  const rawColumns = [
    ...(profile?.deterministic_profile?.columns || []),
    ...(preview?.columns || []),
    ...(source?.columns || []),
  ];
  const columns: string[] = Array.from(
    new Set(
      rawColumns
        .map(columnName)
        .filter((name) => Boolean(name) && !name.startsWith("_")),
    ),
  );
  useEffect(() => {
    if (!columns.length) return;
    setFieldMap((current) => {
      const next = { ...current };
      for (const column of columns) {
        if (!next[column])
          next[column] = {
            target: column,
            role: defaultRole(column, entityColumn, timeColumn, timeKind),
          };
      }
      return next;
    });
  }, [columns.join("|"), entityColumn, timeColumn, timeKind]);
  useEffect(() => {
    const signature = `${source?.id || ""}|${timeKind}|${entityColumn}|${timeColumn}|${fromColumn}|${toColumn}`;
    if (
      timeConfigRef.current &&
      timeConfigRef.current !== signature &&
      profile?.status === "completed"
    ) {
      setProfile(null);
      setError("时间定义已变更；请重新处理数据后再确认本体映射。");
    }
    timeConfigRef.current = signature;
  }, [
    source?.id,
    timeKind,
    entityColumn,
    timeColumn,
    fromColumn,
    toColumn,
    profile?.status,
  ]);
  const suggested = profile?.llm_suggestion || {};
  const canNext =
    step === 0
      ? Boolean(source?.installed)
      : step === 1
        ? Boolean(preview && Number(preview.total_rows) > 0)
        : step === 2
          ? Boolean(profile?.status === "completed")
          : step === 3
            ? relations.some(Boolean)
            : true;
  const execute = async () => {
    if (!source || !profile) return;
    setBusy(true);
    setError("");
    try {
      const r = await apiClientV2.post<any>("/temporal/runs", {
        profile_id: profile.id,
        source_id: source.id,
        dataset_id: source.dataset_id,
        adapter:
          source.id === "cmapss_fd004"
            ? "cmapss"
            : source.id === "factorynet_cnc"
              ? "factorynet"
              : "bts",
        time_kind: timeKind,
        time_precision: "source-defined",
        event_time_column: timeKind === "instant" ? timeColumn : null,
        sequence_column: timeKind === "ordinal" ? timeColumn : null,
        valid_from_column: timeKind === "interval" ? fromColumn : null,
        valid_to_column: timeKind === "interval" ? toColumn : null,
        entity_id_column: entityColumn,
        filters,
        field_mapping: { columns: fieldMap, relations },
        sample_limit: Number(filters.max_records || source.records || 5000),
        privacy_level: privacy,
        ontology_mode: ontologyMode,
        ontology_id: ontologyMode === "reuse" ? ontologyId : null,
        ontology_name: ontologyName,
        ontology_domain: "制造",
      });
      navigate(`/data/temporal/runs/${r.run_id || r.id}`);
    } catch (e: any) {
      setError(errorText(e));
    } finally {
      setBusy(false);
    }
  };
  const timeHelp =
    timeKind === "instant"
      ? "每条记录有真实日期或时间戳，适合日历时间。"
      : timeKind === "ordinal"
        ? "每条记录只有顺序或相对值（如 cycle、step、elapsed seconds），不转换成日期。"
        : "记录在开始和结束之间有效，结束为空表示仍有效；结束早于开始会被拒绝。";
  if (loading)
    return <div className="p-6 text-sm text-gray-500">加载时序数据...</div>;
  return (
    <div className="wb-page max-w-[1320px]">
      <div className="wb-page-header">
        <div>
          <button
            onClick={() => navigate("/data/temporal")}
            className="text-xs text-gray-500 flex gap-1 items-center mb-2"
          >
            <ArrowLeft size={13} />
            返回
          </button>
          <div className="wb-eyebrow">数据构筑 / 时序数据</div>
          <h2 className="wb-page-title">时序数据构建</h2>
          <p className="wb-page-subtitle">
            FactoryNet CNC · Instant / Ordinal / Interval
          </p>
        </div>
        <button onClick={load} className="wb-button-secondary">
          <RefreshCw size={14} />
          刷新历史任务<span className="text-xs text-gray-400">{updatedAt}</span>
        </button>
      </div>
      <div className="wb-stepper wb-surface">
        {steps.map((s, i) => (
          <div
            key={s}
            className={`wb-step min-w-max ${i === step ? "wb-step-active" : i < step ? "wb-step-done" : ""}`}
          >
            <div
              className={`wb-step-index ${i < step ? "bg-emerald-600 text-white" : i === step ? "bg-slate-900 text-white" : ""}`}
            >
              {i < step ? <Check size={14} /> : i + 1}
            </div>
            <span
              className={`${i === step ? "font-medium text-white" : i < step ? "text-gray-700" : "text-gray-400"}`}
            >
              {s}
            </span>
            {i < steps.length - 1 && <div className="w-10 h-px bg-gray-200" />}
          </div>
        ))}
      </div>
      {error && (
        <div className="wb-alert wb-alert-danger">
          <TriangleAlert size={16} />
          {error}
        </div>
      )}
      {step === 0 && (
        <section className="wb-surface p-5 space-y-5">
          <div>
            <p className="text-xs text-gray-400">步骤 1 / 5</p>
            <h3 className="font-semibold mt-1">选择或导入数据</h3>
            <p className="text-sm text-gray-500 mt-1">
              先选择数据源，字段画像与时间定义在后续步骤确认。
            </p>
          </div>
          <div className="grid lg:grid-cols-2 gap-4">
            {sources.map((item) => (
              <div
                key={item.id}
                className={`border rounded-xl p-4 ${source?.id === item.id ? "border-black ring-1 ring-black" : ""}`}
              >
                <div className="flex justify-between gap-3">
                  <div>
                    <h4 className="font-medium">{item.name}</h4>
                    <p className="text-xs text-gray-500 mt-1">
                      {item.id === "factorynet_cnc"
                        ? "CNC 三轴铣削 · 25,286 行 · 18 个生产过程 · 57 个字段"
                        : item.id === "cmapss_fd004"
                          ? "sensor_readings + equipment · cycle 顺序值 · 关联设备表"
                          : "用户上传文件"}
                    </p>
                  </div>
                  <span
                    className={`text-xs px-2 py-1 rounded-full ${item.installed ? "bg-green-50 text-green-700" : "bg-amber-50 text-amber-700"}`}
                  >
                    {item.installed ? "可选择" : "未安装"}
                  </span>
                </div>
                <div className="grid grid-cols-3 gap-2 text-xs mt-3">
                  <span className="bg-gray-50 rounded p-2">
                    记录 {fmt(item.records)}
                  </span>
                  <span className="bg-gray-50 rounded p-2">
                    字段 {item.columns?.length || "—"}
                  </span>
                  <span className="bg-gray-50 rounded p-2">
                    时间{" "}
                    {item.id === "factorynet_cnc" ||
                    item.id === "cmapss_fd004" ||
                    item.time_kind === "ordinal"
                      ? "Ordinal"
                      : "自动识别"}
                  </span>
                </div>
                <p className="text-xs text-gray-500 mt-3">
                  来源：
                  <a
                    className="underline"
                    href={item.source_url || "#"}
                    target="_blank"
                    rel="noreferrer"
                  >
                    官方来源页
                  </a>{" "}
                  · License：{item.license || "CC BY-NC-SA 4.0"}
                </p>
                <p className="text-[11px] text-gray-400 mt-1 break-all">
                  文件：{item.filename || "—"} · SHA-256：
                  {item.sha256 || "安装后显示"}
                </p>
                <button
                  disabled={busy || !item.installed}
                  onClick={() => startAnalysis(item)}
                  className="mt-4 w-full border rounded-lg py-2 text-sm disabled:opacity-40"
                >
                  {source?.id === item.id ? "已选择" : "选择此数据"}
                </button>
                {!item.installed && item.id === "factorynet_cnc" && (
                  <button
                    disabled={busy}
                    onClick={install}
                    className="mt-2 w-full bg-black text-white rounded-lg py-2 text-sm"
                  >
                    {busy ? "安装中..." : "安装官方 FactoryNet 样例"}
                  </button>
                )}
              </div>
            ))}
          </div>
          <div className="border-t pt-4 flex items-center gap-3">
            <input
              ref={uploadRef}
              type="file"
              accept=".csv,.tsv,.json,.jsonl,.xlsx,.xls,.parquet"
              className="hidden"
              onChange={(e) => {
                const f = e.target.files?.[0];
                if (f) upload(f);
              }}
            />
            <button
              disabled={busy}
              onClick={() => uploadRef.current?.click()}
              className="border rounded-lg px-4 py-2 text-sm flex gap-2 items-center"
            >
              <FileUp size={14} />
              {busy ? "处理中..." : "导入本地时序文件"}
            </button>
            <span className="text-xs text-gray-500">
              CSV、TSV、JSON、JSONL、XLSX、Parquet，最大 200 MB
            </span>
          </div>
          {source && (
            <div className="border rounded-lg bg-gray-50 p-4 text-sm">
              <b>当前选择：</b>
              {source.name} ·{" "}
              {source.records || preview?.total_source_rows || "—"} 条记录
              {profile && (
                <span className="ml-3">
                  M3 状态：
                  <b
                    className={
                      profile.status === "completed"
                        ? "text-green-700"
                        : profile.status === "failed"
                          ? "text-red-700"
                          : "text-amber-700"
                    }
                  >
                    {profile.status === "completed"
                      ? "分析完成"
                      : profile.status === "failed"
                        ? "分析失败"
                        : "分析中"}
                  </b>
                </span>
              )}
              {profile?.status === "completed" && (
                <p className="text-xs text-gray-500 mt-2">
                  模型：{profile.model_name || "MiniMax-M3"} · 响应哈希：
                  {profile.response_hash || "—"} · 推荐时间类型：
                  {source?.id === "factorynet_cnc"
                    ? "ordinal（FactoryNet 默认）"
                    : suggested.time_kind || "—"}
                </p>
              )}
              {profile?.status === "failed" && (
                <button
                  onClick={() => startAnalysis(source)}
                  className="ml-3 text-xs underline"
                >
                  重新分析
                </button>
              )}
            </div>
          )}
        </section>
      )}
      {step === 1 && (
        <section className="wb-surface p-5 space-y-5">
          <p className="text-xs text-gray-400">步骤 2 / 5</p>
          <h3 className="font-semibold">筛选数据</h3>
          <p className="text-sm text-gray-500">
            筛选器根据数据画像生成。FactoryNet 默认均匀覆盖全部 episode；本地
            Sensor Reading 默认使用完整记录数。
          </p>
          <div className="grid md:grid-cols-3 gap-3">
            <label className="text-sm">
              最大记录数
              <input
                type="number"
                min="1"
                max={source?.records || 1000000}
                value={filters.max_records || ""}
                onChange={(e) => updateMax(e.target.value)}
                className="mt-1 w-full border rounded-lg px-3 py-2"
              />
            </label>
            <label className="text-sm">
              系列/实体筛选
              <input
                value={filters.equals?.[entityColumn] || ""}
                onChange={(e) => {
                  const n = {
                    ...filters,
                    equals: {
                      ...filters.equals,
                      [entityColumn]: e.target.value,
                    },
                  };
                  setFilters(n);
                  query(n);
                }}
                placeholder={entityColumn}
                className="mt-1 w-full border rounded-lg px-3 py-2"
              />
            </label>
            <label className="text-sm">
              时间最小值
              <input
                type="number"
                value={filters.ranges?.[timeColumn]?.min || ""}
                onChange={(e) => {
                  const n = {
                    ...filters,
                    ranges: {
                      ...filters.ranges,
                      [timeColumn]: {
                        ...filters.ranges?.[timeColumn],
                        min: e.target.value,
                      },
                    },
                  };
                  setFilters(n);
                  query(n);
                }}
                className="mt-1 w-full border rounded-lg px-3 py-2"
              />
            </label>
          </div>
          <div className="border rounded-lg bg-gray-50 p-4 text-sm">
            筛选后预计 <b>{preview?.total_rows ?? "—"}</b> 条，原始数据{" "}
            <b>{preview?.total_source_rows ?? source?.records ?? "—"}</b>{" "}
            条；当前实体列：<b>{entityColumn}</b>
          </div>
          <div className="overflow-auto border rounded-lg max-h-80">
            <table className="w-full text-xs">
              <thead className="bg-gray-50 sticky top-0">
                <tr>
                  {columns.slice(0, 12).map((c: string) => (
                    <th
                      key={c}
                      className="text-left px-3 py-2 whitespace-nowrap"
                    >
                      {c}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {(preview?.rows || []).map((r: any, i: number) => (
                  <tr key={i} className="border-t">
                    {columns.slice(0, 12).map((c: string) => (
                      <td
                        key={c}
                        className="px-3 py-2 whitespace-nowrap max-w-[180px] truncate"
                      >
                        {fmt(r[c])}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </section>
      )}
      {step === 2 && (
        <section className="wb-surface p-5 space-y-5">
          <p className="text-xs text-gray-400">步骤 3 / 5</p>
          <h3 className="font-semibold">定义时间语义</h3>
          <p className="text-sm text-gray-500">
            选择时间列的语义；系统不会凭空生成日期。
          </p>
          <div className="grid md:grid-cols-2 gap-3">
            <button
              type="button"
              onClick={() => setPrivacy("standard")}
              className={`text-left border rounded-lg p-4 flex gap-3 ${privacy === "standard" ? "border-black ring-1 ring-black" : ""}`}
            >
              <ShieldCheck size={17} className="mt-0.5 text-blue-600" />
              <span>
                <strong className="text-sm">标准 standard</strong>
                <span className="block text-xs text-gray-500 mt-1">
                  确认后的字段摘要可发送给 MiniMax M3
                </span>
              </span>
            </button>
            <button
              type="button"
              onClick={() => setPrivacy("private")}
              className={`text-left border rounded-lg p-4 flex gap-3 ${privacy === "private" ? "border-black ring-1 ring-black" : ""}`}
            >
              <ShieldCheck size={17} className="mt-0.5 text-emerald-600" />
              <span>
                <strong className="text-sm">私密 private</strong>
                <span className="block text-xs text-gray-500 mt-1">
                  不调用云模型，仅使用规则与人工映射
                </span>
              </span>
            </button>
          </div>
          <div className="grid md:grid-cols-3 gap-3">
            {[
              [
                "instant",
                "Instant · 时间点",
                "真实日期或时间戳，例如 2026-09-01 10:00",
              ],
              [
                "ordinal",
                "Ordinal · 顺序值",
                "cycle、step、elapsed seconds 等相对顺序，不代表日历日期",
              ],
              [
                "interval",
                "Interval · 有效区间",
                "状态在 [开始,结束) 内有效，结束为空表示仍有效",
              ],
            ].map(([k, t, d]) => (
              <button
                key={k}
                onClick={() => setTimeKind(k as any)}
                className={`text-left border rounded-lg p-4 ${timeKind === k ? "border-black ring-1 ring-black" : ""}`}
              >
                <p className="font-medium">{t}</p>
                <p className="text-xs text-gray-500 mt-2">{d}</p>
              </button>
            ))}
          </div>
          <div className="grid md:grid-cols-3 gap-3 text-sm">
            <label>
              实体/系列 ID 列
              <select
                value={entityColumn}
                onChange={(e) => setEntityColumn(e.target.value)}
                className="mt-1 w-full border rounded-lg px-3 py-2"
              >
                {["_series_id", ...columns].map((c) => (
                  <option key={c}>{c}</option>
                ))}
              </select>
            </label>
            {timeKind !== "interval" && (
              <label>
                {timeKind === "instant" ? "时间列" : "顺序列"}
                <select
                  value={timeColumn}
                  onChange={(e) => setTimeColumn(e.target.value)}
                  className="mt-1 w-full border rounded-lg px-3 py-2"
                >
                  {["_event_seq", ...columns].map((c) => (
                    <option key={c}>{c}</option>
                  ))}
                </select>
              </label>
            )}
            {timeKind === "interval" && (
              <>
                <label>
                  开始列
                  <select
                    value={fromColumn}
                    onChange={(e) => setFromColumn(e.target.value)}
                    className="mt-1 w-full border rounded-lg px-3 py-2"
                  >
                    {columns.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </label>
                <label>
                  结束列
                  <select
                    value={toColumn}
                    onChange={(e) => setToColumn(e.target.value)}
                    className="mt-1 w-full border rounded-lg px-3 py-2"
                  >
                    {columns.map((c) => (
                      <option key={c}>{c}</option>
                    ))}
                  </select>
                </label>
              </>
            )}
          </div>
          <div className="bg-blue-50 text-blue-800 rounded-lg p-4 text-sm">
            {timeHelp} 当前配置：<b>{timeKind}</b> · 实体列{" "}
            <b>{entityColumn}</b> ·{" "}
            {timeKind === "interval"
              ? `${fromColumn} → ${toColumn}`
              : timeColumn}
          </div>
          <div className="border rounded-lg bg-gray-50 p-4 flex items-center justify-between gap-3">
            <div className="text-sm">
              <b>确定性画像与字段建议</b>
              <p className="text-xs text-gray-500 mt-1">
                {privacy === "private"
                  ? "私密模式不会调用 MiniMax M3。"
                  : "标准模式仅在明确点击后调用 MiniMax M3。"}
              </p>
            </div>
            <button
              type="button"
              onClick={processAnalysis}
              disabled={busy || profile?.status === "completed"}
              className="bg-black text-white rounded-lg px-4 py-2 text-sm flex items-center gap-2 disabled:opacity-40"
            >
              <Play size={14} />
              {profile?.status === "completed"
                ? "已完成"
                : busy
                  ? "处理中"
                  : "开始处理"}
            </button>
          </div>
          {profile && (
            <div className="text-xs text-gray-500">
              画像状态：
              <b
                className={
                  profile.status === "completed"
                    ? "text-emerald-700"
                    : profile.status === "failed"
                      ? "text-red-700"
                      : "text-amber-700"
                }
              >
                {profile.status === "completed"
                  ? "已完成"
                  : profile.status === "failed"
                    ? "失败"
                    : "处理中"}
              </b>
              {profile.llm_used ? " · 已使用 MiniMax M3" : " · 未调用云模型"}
              {profile.error ? ` · ${profile.error}` : ""}
            </div>
          )}
        </section>
      )}
      {step === 3 && (
        <section className="wb-surface p-5 space-y-5">
          <p className="text-xs text-gray-400">步骤 4 / 5</p>
          <h3 className="font-semibold">编辑本体映射</h3>
          <p className="text-sm text-gray-500">
            这里决定源字段进入哪些本体属性，以及哪些关系连接节点。修改只影响本次构建。
          </p>
          <div className="grid xl:grid-cols-[1fr_360px] gap-5">
            <div className="border rounded-lg overflow-auto">
              <table className="w-full text-xs">
                <thead className="bg-gray-50">
                  <tr>
                    <th className="text-left px-3 py-2">源列</th>
                    <th className="text-left px-3 py-2">目标属性</th>
                    <th className="text-left px-3 py-2">时序角色</th>
                    <th className="text-left px-3 py-2">建议来源</th>
                    <th className="text-left px-3 py-2">置信度</th>
                  </tr>
                </thead>
                <tbody>
                  {columns.map((c: string) => (
                    <tr key={c} className="border-t">
                      <td className="px-3 py-2 font-mono">{c}</td>
                      <td className="px-3 py-2">
                        <input
                          value={fieldMap[c]?.target || c}
                          onChange={(e) =>
                            setFieldMap({
                              ...fieldMap,
                              [c]: {
                                target: e.target.value,
                                role:
                                  fieldMap[c]?.role ||
                                  defaultRole(
                                    c,
                                    entityColumn,
                                    timeColumn,
                                    timeKind,
                                  ),
                              },
                            })
                          }
                          className="border rounded px-2 py-1 w-full"
                        />
                      </td>
                      <td className="px-3 py-2">
                        <select
                          value={
                            fieldMap[c]?.role ||
                            defaultRole(c, entityColumn, timeColumn, timeKind)
                          }
                          onChange={(e) =>
                            setFieldMap({
                              ...fieldMap,
                              [c]: {
                                target: fieldMap[c]?.target || c,
                                role: e.target.value,
                              },
                            })
                          }
                          className="border rounded px-2 py-1 w-full"
                        >
                          <option>实体标识</option>
                          <option>Ordinal 顺序</option>
                          <option>Instant 时间点</option>
                          <option>Interval 边界</option>
                          <option>观测属性</option>
                          <option>关联维度</option>
                        </select>
                      </td>
                      <td className="px-3 py-2 text-gray-500">
                        {suggested.measurement_columns?.includes(c) ||
                        suggested.time_column === c ||
                        suggested.entity_column === c
                          ? "MiniMax 建议"
                          : "确定性画像"}
                      </td>
                      <td className="px-3 py-2 text-gray-500">
                        {Number(
                          suggested.confidence_by_column?.[c] ??
                            (suggested.measurement_columns?.includes(c)
                              ? 0.86
                              : 1),
                        ).toFixed(2)}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="space-y-4">
              <div className="border rounded-lg p-4">
                <p className="text-sm font-medium mb-2">关系编辑器</p>
                {relations.map((r, i) => (
                  <div key={`${r}-${i}`} className="flex gap-2 mb-2">
                    <input
                      value={r}
                      onChange={(e) =>
                        setRelations(
                          relations.map((x, j) =>
                            j === i ? e.target.value : x,
                          ),
                        )
                      }
                      className="border rounded px-2 py-1 text-xs flex-1"
                    />
                    <button
                      onClick={() =>
                        setRelations(relations.filter((_, j) => j !== i))
                      }
                      className="text-xs text-red-600"
                    >
                      删除
                    </button>
                  </div>
                ))}
                <button
                  onClick={() => setRelations([...relations, "NEW_RELATION"])}
                  className="text-xs border rounded px-2 py-1"
                >
                  新增关系
                </button>
              </div>
              <div className="border rounded-lg p-4 bg-gray-50 text-xs">
                <p className="font-medium">默认结构</p>
                <p className="mt-2">Machine → Episode → Observation</p>
                <p>Observation → Machine / ProcessPhase / ToolCondition</p>
                <p>Observation → NEXT_OBSERVATION → Observation</p>
                <p className="mt-2">数值列作为 Observation 属性保存。</p>
              </div>
            </div>
          </div>
        </section>
      )}
      {step === 4 && (
        <section className="wb-surface p-5 space-y-5">
          <p className="text-xs text-gray-400">步骤 5 / 5</p>
          <h3 className="font-semibold">确认并构建</h3>
          <div className="grid md:grid-cols-2 gap-4">
            <div className="border rounded-lg p-4 text-sm space-y-2">
              <p className="font-medium">本次配置</p>
              <p>数据：{source?.name}</p>
              <p>记录：{preview?.total_rows ?? filters.max_records}</p>
              <p>
                时间：{timeKind} ·{" "}
                {timeKind === "interval"
                  ? `${fromColumn} → ${toColumn}`
                  : timeColumn}
              </p>
              <p>实体：{entityColumn}</p>
              <p>关系：{relations.filter(Boolean).join("、")}</p>
            </div>
            <div className="border rounded-lg p-4 text-sm space-y-3">
              <p className="font-medium">目标本体</p>
              <label className="flex gap-2 items-center">
                <input
                  type="radio"
                  checked={ontologyMode === "create"}
                  onChange={() => setOntologyMode("create")}
                />
                创建新本体
              </label>
              {ontologyMode === "create" && (
                <input
                  value={ontologyName}
                  onChange={(e) => setOntologyName(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2"
                />
              )}
              <label className="flex gap-2 items-center">
                <input
                  type="radio"
                  checked={ontologyMode === "reuse"}
                  onChange={() => setOntologyMode("reuse")}
                />
                添加到已有本体
              </label>
              {ontologyMode === "reuse" && (
                <select
                  value={ontologyId}
                  onChange={(e) => setOntologyId(e.target.value)}
                  className="w-full border rounded-lg px-3 py-2"
                >
                  <option value="">请选择本体</option>
                  {ontologies.map((o) => (
                    <option key={o.id} value={o.id}>
                      {o.name}
                    </option>
                  ))}
                </select>
              )}
            </div>
          </div>
          <div className="border rounded-lg bg-gray-50 p-4 text-sm">
            执行过程：读取 → 筛选 → 时间校验 → 规范化 → 生成节点 → 生成关系 →
            FalkorDB 写入 → EvidenceRef 保存 → 数量核对。空数据、孤立观测或 0
            条关系会失败并保留可重试任务。
          </div>
          <button
            disabled={
              busy ||
              !canNext ||
              (ontologyMode === "reuse" && !ontologyId) ||
              !ontologyName.trim()
            }
            onClick={execute}
            className="bg-black text-white rounded-lg px-5 py-2.5 text-sm flex gap-2 items-center disabled:opacity-40"
          >
            {busy ? (
              <Loader2 size={15} className="animate-spin" />
            ) : (
              <Play size={15} />
            )}
            开始构建
          </button>
        </section>
      )}
      <section className="wb-surface p-5">
        <div className="flex items-center justify-between mb-3">
          <h3 className="font-semibold">历史构建任务</h3>
          <button
            type="button"
            onClick={() => setShowHistory((value) => !value)}
            className="text-xs text-slate-500 underline hover:text-slate-900"
          >
            {showHistory ? "收起历史任务" : `显示历史任务（${history.length}）`}
          </button>
        </div>
        {!showHistory ? (
          <p className="text-sm text-gray-500">历史任务默认收起。</p>
        ) : history.length === 0 ? (
          <p className="text-sm text-gray-500">暂无历史任务</p>
        ) : (
          <div className="space-y-2">
            {history.map((r: any) => (
              <button
                key={r.id || r.run_id}
                onClick={() =>
                  navigate(`/data/temporal/runs/${r.id || r.run_id}`)
                }
                className="w-full text-left border rounded-lg px-3 py-2 hover:bg-gray-50"
              >
                <div className="flex justify-between text-sm">
                  <span>
                    {r.config?.source_id || r.config?.source || "时序数据"} ·{" "}
                    {r.config?.adapter || "generic"}
                  </span>
                  <span
                    className={
                      r.status === "completed"
                        ? "text-green-700"
                        : r.status === "failed"
                          ? "text-red-700"
                          : "text-amber-700"
                    }
                  >
                    {r.status}
                  </span>
                </div>
                <p className="text-xs text-gray-500 mt-1">
                  {r.metrics?.rows_selected ??
                    r.metrics?.rows_normalized ??
                    "—"}{" "}
                  条记录 · {r.metrics?.nodes_upserted ?? 0} 节点 ·{" "}
                  {r.metrics?.edges_upserted ?? 0} 关系 · {r.created_at || ""}
                </p>
              </button>
            ))}
          </div>
        )}
      </section>
      <div className="flex justify-between">
        <button
          onClick={() =>
            step ? setStep(step - 1) : navigate("/data/temporal")
          }
          className="border rounded-lg px-4 py-2 text-sm"
        >
          {step ? "上一步" : "取消"}
        </button>
        {step < 4 && (
          <button
            disabled={!canNext}
            onClick={() => setStep(step + 1)}
            className="bg-black text-white rounded-lg px-5 py-2 text-sm disabled:opacity-40"
          >
            下一步
          </button>
        )}
      </div>
    </div>
  );
}
