/* eslint-disable @typescript-eslint/no-explicit-any */
import { useQuery } from "@tanstack/react-query";
import { Clock3, X } from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Change = { id: string; target_kind: string; operation: string; target_id?: string; before?: any; after?: any; validation?: any; base_revision_id?: string; result_revision_id?: string; created_at?: string; note?: string };

export default function ChangeHistoryDrawer({ ontologyId, onClose }: { ontologyId: string; onClose: () => void }) {
  const { data, isLoading } = useQuery({ queryKey: ["ontology-changes", ontologyId], queryFn: () => apiClientV2.get<{ changes: Change[] }>(`/ontologies/${ontologyId}/changes`) });
  const restore = async (revisionId?: string) => {
    if (!revisionId) return;
    try { await apiClientV2.post(`/ontologies/${ontologyId}/revisions/${revisionId}/restore`); window.location.reload(); } catch { /* the drawer remains open with the historical record */ }
  };
  return <aside className="fixed inset-y-0 right-0 z-40 flex w-full max-w-[420px] flex-col border-l border-slate-200 bg-white shadow-xl" data-testid="change-history-drawer">
    <div className="flex items-center justify-between border-b border-slate-200 px-4 py-3"><div className="flex items-center gap-2"><Clock3 size={16} /><h2 className="text-sm font-semibold text-slate-900">修改记录</h2></div><button onClick={onClose} className="rounded p-1 text-slate-500 hover:bg-slate-100"><X size={16} /></button></div>
    <div className="flex-1 overflow-y-auto p-4">{isLoading ? <p className="text-xs text-slate-500">正在读取记录</p> : <div className="space-y-3">{(data?.changes || []).map((change) => <article key={change.id} className="rounded-lg border border-slate-200 p-3"><div className="flex items-center justify-between gap-2"><strong className="text-xs text-slate-800">{change.operation === "add" ? "新增" : change.operation === "update" ? "修改" : change.operation === "restore" ? "恢复" : "删除"} · {change.target_kind}</strong><span className="text-[10px] text-slate-400">{change.created_at ? new Date(change.created_at).toLocaleString() : "—"}</span></div><p className="mt-1 break-all text-[11px] text-slate-500">对象 {change.target_id || "—"} · 修订 {change.result_revision_id || "—"}</p>{change.note && <p className="mt-2 text-xs text-slate-600">{change.note}</p>}<details className="mt-2"><summary className="cursor-pointer text-[11px] text-slate-500">查看差异</summary><pre className="mt-2 max-h-44 overflow-auto whitespace-pre-wrap rounded bg-slate-50 p-2 text-[10px] text-slate-600">{JSON.stringify({ before: change.before, after: change.after, validation: change.validation }, null, 2)}</pre></details>{change.base_revision_id && <button onClick={() => void restore(change.base_revision_id)} className="mt-3 rounded-md border border-slate-300 px-2.5 py-1.5 text-[11px] text-slate-700 hover:border-slate-600">恢复为新修订</button>}</article>)}{!data?.changes?.length && <p className="py-8 text-center text-xs text-slate-400">暂无修改记录</p>}</div>}</div>
  </aside>;
}
