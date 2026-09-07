import { useCallback, useEffect, useState } from "react";
import { AlertCircle, CheckCircle2, Loader2, XCircle } from "lucide-react";
import { apiClientV2 } from "@/api/client";

type AuditFinding = {
  title?: string;
  category?: string;
  description?: string;
  affected_items?: string[];
  disposition?: string;
};

type AuditTraceStep = {
  step?: number;
  tool_name?: string;
  tool_args?: Record<string, unknown>;
  observation?: string;
  error?: string;
};

type AuditTask = {
  id: string;
  ontology_id: string;
  model_name?: string;
  status: string;
  progress?: { stage?: string; pct?: number };
  error?: string | null;
  findings?: AuditFinding[];
  react_trace?: AuditTraceStep[];
  repair_draft?: { changes?: unknown[] } | null;
};

function messageFor(error: unknown) {
  const detail = error as {
    response?: { data?: { detail?: unknown } };
    detail?: unknown;
    message?: unknown;
  };
  const message =
    detail.response?.data?.detail ||
    detail.detail ||
    detail.message;
  return typeof message === "string" ? message : "请求失败";
}

function statusLabel(status: string) {
  return (
    {
      queued: "排队中",
      running: "审查中",
      cancel_requested: "正在取消",
      cancelled: "已取消",
      completed: "已完成",
      waiting_for_model: "等待本地模型",
      failed: "失败",
    }[status] || status
  );
}

function statusClass(status: string) {
  if (status === "completed") return "wb-status-success";
  if (status === "failed" || status === "cancelled") return "wb-status-danger";
  return "wb-status-warning";
}

function displayText(value: unknown, fallback = "") {
  if (Array.isArray(value)) return value.map((item) => String(item)).join(" · ");
  return value === null || value === undefined ? fallback : String(value);
}

function ToolTrace({ trace, active }: { trace: AuditTraceStep[]; active: boolean }) {
  const [open, setOpen] = useState(true);
  const submittedSummary = (step: AuditTraceStep) => {
    try {
      const observation = typeof step.observation === "string" ? JSON.parse(step.observation) : step.observation;
      if (observation && typeof observation === "object" && "grounded_count" in observation) {
        const submitted = Number((observation as Record<string, unknown>).submitted_count || 0);
        const grounded = Number((observation as Record<string, unknown>).grounded_count || 0);
        if ((observation as Record<string, unknown>).status === "fallback") {
          return "本地模型未返回结构化建议；已完成只读核验（0 项）";
        }
        return `模型提交 ${submitted} 项建议；证据核验后 ${grounded} 项有效`;
      }
    } catch {
      // Older audit records only retain the original tool arguments.
    }
    return `模型提交 ${Array.isArray(step.tool_args?.findings) ? step.tool_args.findings.length : 0} 项建议`;
  };
  if (!trace.length && !active) return null;
  return (
    <section className="rounded-lg border border-slate-200 bg-slate-50 p-3">
      <button
        type="button"
        className="flex w-full items-center justify-between text-sm font-medium text-slate-700"
        onClick={() => setOpen((value) => !value)}
      >
        <span>工具轨迹</span>
        <span className="text-xs font-normal text-slate-500">{trace.length} 步</span>
      </button>
      {open && (
        <div className="mt-3 space-y-2">
          {trace.map((step, index) => (
            <article key={`${step.step ?? index}-${step.tool_name || "step"}`} className="rounded border border-slate-200 bg-white p-3 text-xs">
              <p className="font-medium text-slate-700">
                {String((step.step ?? index) + 1).padStart(2, "0")}
                {step.tool_name ? ` · ${step.tool_name}` : " · 审查步骤"}
              </p>
              {step.tool_name === "submit_findings" ? (
                <p className="mt-1 text-slate-500">
                  结论：{submittedSummary(step)}
                </p>
              ) : step.tool_args && Object.keys(step.tool_args).length > 0 ? (
                <p className="mt-1 break-words text-slate-500">
                  参数：{JSON.stringify(step.tool_args)}
                </p>
              ) : null}
              {step.observation && (
                <p className="mt-1 whitespace-pre-wrap break-words text-slate-600">
                  观察：{step.observation}
                </p>
              )}
              {step.error && <p className="mt-1 text-red-700">{step.error}</p>}
            </article>
          ))}
          {active && (
            <div className="flex items-center gap-2 rounded border border-dashed border-slate-300 bg-white px-3 py-2 text-xs text-slate-500">
              <Loader2 size={13} className="animate-spin" />
              等待下一步工具结果
            </div>
          )}
        </div>
      )}
    </section>
  );
}

export default function AuditTab({ ontologyId }: { ontologyId: string }) {
  const [task, setTask] = useState<AuditTask | null>(null);
  const [selected, setSelected] = useState<number[]>([]);
  const [busy, setBusy] = useState(false);
  const [message, setMessage] = useState("");
  const activeTaskStatus = task?.status;

  const load = useCallback(async () => {
    try {
      const result = await apiClientV2.get<{ tasks?: AuditTask[] }>("/audits", {
        params: { ontology_id: ontologyId },
      });
      setTask(result.tasks?.[0] || null);
    } catch (error) {
      setMessage(messageFor(error));
    }
  }, [ontologyId]);

  useEffect(() => {
    const timer = window.setTimeout(() => void load(), 0);
    return () => window.clearTimeout(timer);
  }, [load]);
  useEffect(() => {
    if (!activeTaskStatus || !["queued", "running", "cancel_requested"].includes(activeTaskStatus)) return;
    const timer = window.setInterval(() => void load(), 1600);
    return () => window.clearInterval(timer);
  }, [activeTaskStatus, load, task?.id]);

  const start = async () => {
    setBusy(true);
    setMessage("");
    try {
      setTask(await apiClientV2.post<AuditTask>("/audits", { ontology_id: ontologyId }));
      setSelected([]);
    } catch (error) {
      setMessage(messageFor(error));
    } finally {
      setBusy(false);
    }
  };

  const cancel = async () => {
    if (!task) return;
    setBusy(true);
    try {
      setTask(await apiClientV2.post<AuditTask>(`/audits/${task.id}/cancel`));
    } catch (error) {
      setMessage(messageFor(error));
    } finally {
      setBusy(false);
    }
  };

  const retry = async () => {
    if (!task) return;
    setBusy(true);
    setMessage("");
    try {
      setTask(await apiClientV2.post<AuditTask>(`/audits/${task.id}/retry`));
      setSelected([]);
    } catch (error) {
      setMessage(messageFor(error));
    } finally {
      setBusy(false);
    }
  };

  const ignore = async (index: number) => {
    if (!task) return;
    setBusy(true);
    try {
      setTask(
        await apiClientV2.post<AuditTask>(
          `/audits/${task.id}/findings/${index}/ignore?note=${encodeURIComponent("人工确认后忽略")}`,
        ),
      );
      setSelected((current) => current.filter((value) => value !== index));
    } catch (error) {
      setMessage(messageFor(error));
    } finally {
      setBusy(false);
    }
  };

  const createRepairDraft = async () => {
    if (!task || selected.length === 0) return;
    setBusy(true);
    try {
      const result = await apiClientV2.post<{ draft?: AuditTask["repair_draft"] }>(
        `/audits/${task.id}/repair-draft`,
        {
          finding_indices: selected,
          note: "由质量审查页勾选生成",
        },
      );
      setTask((current) =>
        current ? { ...current, repair_draft: result.draft || null } : current,
      );
      setMessage("修订草案已生成");
    } catch (error) {
      setMessage(messageFor(error));
    } finally {
      setBusy(false);
    }
  };

  const confirmRepairDraft = async () => {
    if (!task?.repair_draft) return;
    setBusy(true);
    try {
      await apiClientV2.post(`/audits/${task.id}/repair-draft/confirm`, {
        note: "质量审查页确认修订",
      });
      setMessage("已创建新修订并排队复审");
      await load();
    } catch (error) {
      setMessage(messageFor(error));
    } finally {
      setBusy(false);
    }
  };

  if (!task) {
    return (
      <section className="wb-surface p-5">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div>
            <div className="wb-section-kicker">自动审查</div>
            <h2 className="wb-section-title mt-1">质量审查</h2>
            <p className="mt-2 text-sm text-slate-500">当前本体尚无审查记录。</p>
          </div>
          <button type="button" className="wb-button-primary" disabled={busy} onClick={start}>
            {busy && <Loader2 size={14} className="animate-spin" />}
            开始本地审查
          </button>
        </div>
        {message && <p className="mt-3 text-xs text-red-700">{message}</p>}
      </section>
    );
  }

  const findings = Array.isArray(task.findings) ? task.findings : [];
  const active = ["queued", "running", "cancel_requested"].includes(task.status);
  const pct = task.progress?.pct ?? 0;
  return (
    <section className="wb-surface p-5 space-y-4">
      <div className="flex flex-wrap items-start justify-between gap-3">
        <div>
          <div className="wb-section-kicker">自动审查 · {task.model_name || "qwen3.5:0.8b"}</div>
          <h2 className="wb-section-title mt-1">质量审查</h2>
          <p className="mt-1 text-xs text-slate-500">{task.progress?.stage || "等待任务开始"}</p>
        </div>
        <span className={`wb-status ${statusClass(task.status)}`}>
          {statusLabel(task.status)} · {pct}%
        </span>
      </div>

      {active && (
        <div className="space-y-2">
          <div className="h-1.5 overflow-hidden rounded bg-slate-100">
            <div className="h-full bg-blue-600 transition-all duration-500" style={{ width: `${pct}%` }} />
          </div>
          <div className="flex items-center gap-2 text-xs text-slate-500">
            <Loader2 size={13} className="animate-spin" />
            任务正在读取本体内容并执行检查
          </div>
        </div>
      )}
      {task.error && (
        <div className="wb-alert wb-alert-warning text-xs">
          <AlertCircle size={14} />
          {task.error}
        </div>
      )}

      <ToolTrace trace={task.react_trace || []} active={active} />

      {task.status === "completed" && (
        <div className="space-y-2">
          <div className="flex items-center justify-between">
            <span className="text-sm font-medium">审查发现 · {findings.length}</span>
            {findings.length === 0 && <CheckCircle2 size={17} className="text-emerald-600" />}
          </div>
          {findings.length === 0 ? (
            <div className="wb-empty py-5">未发现需要处理的项目</div>
          ) : (
            findings.map((finding, index) => (
              <article
                key={`${displayText(finding.title) || finding.category || "finding"}-${index}`}
                className={`rounded border p-3 ${finding.disposition === "ignored" ? "border-slate-200 bg-slate-50 opacity-60" : "border-slate-200 bg-white"}`}
              >
                <div className="flex items-start gap-2">
                  <input
                    aria-label={`选择发现 ${index + 1}`}
                    type="checkbox"
                    disabled={busy || finding.disposition === "ignored"}
                    checked={selected.includes(index)}
                    onChange={() =>
                      setSelected((current) =>
                        current.includes(index)
                          ? current.filter((value) => value !== index)
                          : [...current, index],
                      )
                    }
                  />
                  <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium text-slate-800">{displayText(finding.title, finding.category || `发现 ${index + 1}`)}</p>
                    {finding.description && <p className="mt-1 text-xs text-slate-500">{displayText(finding.description)}</p>}
                    {finding.affected_items?.length ? <p className="mt-2 text-xs text-slate-500">涉及：{finding.affected_items.join(" · ")}</p> : null}
                  </div>
                  {finding.disposition === "ignored" ? (
                    <span className="text-xs text-slate-400">已忽略</span>
                  ) : (
                    <button type="button" disabled={busy} className="text-xs text-slate-500 underline" onClick={() => void ignore(index)}>忽略</button>
                  )}
                </div>
              </article>
            ))
          )}
        </div>
      )}

      <div className="flex flex-wrap items-center gap-2 border-t border-slate-100 pt-3">
        {active && <button type="button" className="wb-button-secondary text-xs" disabled={busy} onClick={cancel}>取消审查</button>}
        {["failed", "waiting_for_model", "cancelled"].includes(task.status) && <button type="button" className="wb-button-secondary text-xs" disabled={busy} onClick={retry}>重新审查</button>}
        {task.status === "completed" && findings.length > 0 && (
          <button type="button" className="wb-button-secondary text-xs" disabled={busy || selected.length === 0} onClick={createRepairDraft}>生成修订草案</button>
        )}
        {task.repair_draft && (
          <button type="button" className="wb-button-primary text-xs" disabled={busy} onClick={confirmRepairDraft}>确认并复审</button>
        )}
        {task.status === "completed" && <button type="button" className="wb-button-secondary text-xs" disabled={busy} onClick={start}>再次审查</button>}
      </div>
      {message && <p className="text-xs text-emerald-700">{message}</p>}
      {task.status === "failed" && <p className="flex items-center gap-1 text-xs text-red-700"><XCircle size={13} />任务未完成；修复模型服务后可重新审查。</p>}
    </section>
  );
}
