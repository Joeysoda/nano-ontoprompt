import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { apiClientV2 } from "@/api/client";

type Entity = { id: string; name?: string; api_name?: string; display_name?: string };
type LoadResult = { objects: Array<{ object_type: string; object_id: string; properties: Record<string, unknown> }>; page: { has_more: boolean; next_page_token?: string | null }; completeness: string; status: string };

export default function ObjectQueryTab({ ontologyId }: { ontologyId: string }) {
  const [typeName, setTypeName] = useState("");
  const [pageToken, setPageToken] = useState<string | null>(null);
  const entities = useQuery({
    queryKey: ["object-query-types", ontologyId],
    queryFn: () => apiClientV2.get<{ entities: Entity[] }>(`/ontologies/${ontologyId}/entities`),
  });
  const types = useMemo(() => entities.data?.entities || [], [entities.data?.entities]);
  const selected = useMemo(() => typeName || types[0]?.api_name || types[0]?.name || "", [typeName, types]);
  const result = useQuery({
    queryKey: ["object-query-load", ontologyId, selected, pageToken],
    enabled: !!selected && entities.isSuccess,
    queryFn: () => apiClientV2.post<LoadResult>(`/ontologies/${ontologyId}/object-query/load`, {
      expression: { kind: "base", type_ref: { kind: "object", api_name: selected } },
      context: { ontology_id: ontologyId, consistency: "live" },
      read: { page_size: 50, page_token: pageToken },
    }),
  });
  if (entities.isPending) return <div role="status" className="rounded border p-4 text-sm text-slate-500">正在加载对象类型</div>;
  if (entities.error) return <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">对象类型加载失败。<button onClick={() => void entities.refetch()}>重试</button></div>;
  if (!selected) return <div role="status">暂无可查询的对象类型。</div>;
  return <section aria-label="对象查询" className="space-y-4">
    <div className="flex flex-wrap items-end gap-3 rounded border bg-white p-4">
      <label className="text-sm text-slate-700">对象类型
        <select value={selected} onChange={(event) => { setTypeName(event.target.value); setPageToken(null); }} className="ml-2 rounded border px-2 py-1.5">
          {types.map((entity) => <option key={entity.api_name || entity.id || entity.name} value={entity.api_name || entity.name || ""}>{entity.display_name || entity.name || entity.api_name}</option>)}
        </select>
      </label>
      <span className="text-xs text-slate-500">当前为 live preview；完整 what-if 推理需要 pinned snapshot。</span>
    </div>
    {result.isLoading && <div role="status" className="p-4 text-sm text-slate-500">正在读取对象</div>}
    {result.error && <div role="alert" className="rounded border border-red-200 bg-red-50 p-4 text-sm text-red-700">对象查询失败。<button onClick={() => void result.refetch()}>重试</button></div>}
    {result.data?.page.has_more && !result.data.page.next_page_token && <p role="status">仅显示 live 预览的首批对象；完整集合需要固定快照，当前结果不能作为完整推理输入。</p>}
    {result.data && <>
      <div className="overflow-auto rounded border bg-white">
        <table className="min-w-full text-left text-sm"><thead><tr className="border-b bg-slate-50"><th className="px-3 py-2">对象 ID</th><th className="px-3 py-2">属性</th></tr></thead>
          <tbody>{result.data.objects.map((item) => <tr key={`${item.object_type}:${item.object_id}`} className="border-b last:border-0"><td className="px-3 py-2 font-mono text-xs">{item.object_id}</td><td className="px-3 py-2"><pre className="whitespace-pre-wrap text-xs">{JSON.stringify(item.properties, null, 2)}</pre></td></tr>)}</tbody>
        </table>
      </div>
      <div className="flex items-center justify-between text-xs text-slate-500"><span>{result.data.objects.length} 条，状态：{result.data.status}，完整性：{result.data.completeness}</span><button disabled={!result.data.page.has_more || !result.data.page.next_page_token} onClick={() => setPageToken(result.data.page.next_page_token || null)} className="rounded border px-3 py-1.5 disabled:opacity-40">下一页</button></div>
    </>}
  </section>;
}
