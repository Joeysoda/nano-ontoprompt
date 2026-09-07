import { useCallback, useEffect, useState } from "react";
import { useNavigate, useParams } from "react-router-dom";
import {
  ArrowLeft,
  CheckCircle2,
  Loader2,
  RefreshCw,
  TriangleAlert,
} from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Run = {
  id: string;
  ontology_id: string;
  status: string;
  metrics?: Record<string, any>;
  progress?: {
    stage?: string;
    pct?: number;
    completed?: number;
    total?: number;
  };
  error?: string;
};

const formatValue = (value: unknown) =>
  value === null || value === undefined || value === "" ? "—" : String(value);

const taskTitle = (status: string) => {
  if (status === "completed") return "构建完成";
  if (status === "failed") return "构建失败";
  if (status === "cancelled") return "已取消";
  if (status === "waiting_for_model") return "等待模型";
  if (status === "queued") return "排队中";
  return "处理中";
};

export default function TemporalWorkbenchPage() {
  const { runId } = useParams<{ runId: string }>();
  const navigate = useNavigate();
  const [run, setRun] = useState<Run | null>(null);
  const [error, setError] = useState("");

  const load = useCallback(async () => {
    if (!runId) return;
    try {
      setError("");
      setRun(await apiClientV2.get<Run>(`/construction-runs/${runId}`));
    } catch (reason: any) {
      setError(
        reason?.response?.data?.detail || reason?.message || "无法读取构建状态",
      );
    }
  }, [runId]);

  useEffect(() => {
    load();
  }, [load]);

  useEffect(() => {
    if (!run || ["completed", "failed", "cancelled"].includes(run.status))
      return;
    const timer = window.setInterval(load, 1200);
    return () => window.clearInterval(timer);
  }, [load, run?.status]);

  if (!run && !error) {
    return (
      <div className="flex min-h-56 items-center gap-2 text-sm text-slate-500">
        <Loader2 className="h-4 w-4 animate-spin" /> 读取构建任务
      </div>
    );
  }

  const status = run?.status || "failed";
  const progress = run?.progress || {};
  const percent = Math.max(
    0,
    Math.min(100, Number(progress.pct ?? (status === "completed" ? 100 : 0))),
  );
  const summary = run?.metrics?.summary || {};
  const measures = [
    ["源记录", run?.metrics?.rows_in],
    ["选中记录", run?.metrics?.rows_selected ?? run?.metrics?.rows_normalized],
    ["实体", run?.metrics?.entities_upserted ?? run?.metrics?.nodes_upserted],
    ["关系", run?.metrics?.relations_upserted ?? run?.metrics?.edges_upserted],
    ["序列", summary.episodes],
    ["时序问题", run?.metrics?.temporal_issues ?? 0],
  ];
  const isComplete = status === "completed";

  return (
    <div className="mx-auto max-w-6xl space-y-5">
      <div className="flex items-start justify-between gap-4">
        <div>
          <button
            onClick={() => navigate("/data/temporal")}
            className="mb-2 flex items-center gap-1 text-xs text-slate-500 hover:text-slate-900"
          >
            <ArrowLeft size={13} /> 返回时序数据
          </button>
          <h2 className="text-2xl font-semibold text-slate-900">
            时序本体构建
          </h2>
          <p className="mt-1 text-sm text-slate-500">
            {summary.dataset || "FactoryNet CNC"} ·{" "}
            {run?.metrics?.time_kind || "ordinal"}
          </p>
        </div>
        <div className="flex items-center gap-2">
          <button
            onClick={load}
            className="inline-flex items-center gap-2 rounded-lg border border-slate-300 bg-white px-3 py-2 text-sm hover:bg-slate-50"
          >
            <RefreshCw size={14} /> 刷新
          </button>
          {isComplete && run?.ontology_id && (
            <button
              onClick={() => navigate(`/ontologies/${run.ontology_id}`)}
              className="rounded-lg bg-slate-900 px-3 py-2 text-sm text-white hover:bg-slate-700"
            >
              打开本体
            </button>
          )}
        </div>
      </div>

      {error && (
        <div className="flex items-start gap-2 rounded-xl border border-red-200 bg-red-50 px-4 py-3 text-sm text-red-700">
          <TriangleAlert className="mt-0.5 h-4 w-4 shrink-0" />
          <span>{error}</span>
        </div>
      )}

      <section className="rounded-xl border border-slate-200 bg-white p-5">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="flex items-center gap-2">
            {isComplete ? (
              <CheckCircle2 className="h-5 w-5 text-emerald-600" />
            ) : (
              <Loader2 className="h-5 w-5 animate-spin text-blue-600" />
            )}
            <div>
              <p className="font-medium text-slate-900">{taskTitle(status)}</p>
              <p className="mt-0.5 text-sm text-slate-500">
                {run?.error || progress.stage || "等待任务更新"}
              </p>
            </div>
          </div>
          <span className="text-sm font-medium tabular-nums text-slate-700">
            {percent}%
          </span>
        </div>
        <div className="mt-4 h-2 overflow-hidden rounded-full bg-slate-100">
          <div
            className={`h-full rounded-full transition-all ${status === "failed" ? "bg-red-500" : status === "waiting_for_model" ? "bg-amber-500" : "bg-blue-600"}`}
            style={{ width: `${percent}%` }}
          />
        </div>
        <p className="mt-2 text-xs text-slate-500">
          已处理 {formatValue(progress.completed)} /{" "}
          {formatValue(progress.total)}
        </p>
      </section>

      <section className="grid grid-cols-2 gap-3 md:grid-cols-3 xl:grid-cols-6">
        {measures.map(([label, value]) => (
          <div
            key={String(label)}
            className="rounded-xl border border-slate-200 bg-white p-4"
          >
            <p className="text-xs text-slate-500">{label}</p>
            <p className="mt-1 text-xl font-semibold tabular-nums text-slate-900">
              {formatValue(value)}
            </p>
          </div>
        ))}
      </section>

      {isComplete && (
        <section className="rounded-xl border border-emerald-200 bg-emerald-50 px-5 py-4 text-sm text-emerald-900">
          本体已发布。实体、关系、逻辑规则和质量审查在“打开本体”中统一查看。
        </section>
      )}

      {["failed", "cancelled"].includes(status) && (
        <section className="rounded-xl border border-amber-200 bg-amber-50 px-5 py-4 text-sm text-amber-900">
          请返回时序数据，检查时间定义和数据选择后重新构建。
        </section>
      )}
    </div>
  );
}
