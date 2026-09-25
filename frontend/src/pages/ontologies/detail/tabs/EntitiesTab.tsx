import { useState } from "react";
import { useNavigate } from "react-router-dom";
import { useQuery } from "@tanstack/react-query";
import { ChevronDown, ChevronRight, ExternalLink, Loader2 } from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Property = {
  id?: string;
  name?: string;
  label?: string;
  type?: string;
  source_field?: string;
};
type EntityType = {
  id: string;
  name?: string;
  name_cn?: string;
  name_en?: string;
  description?: string;
  properties?: Property[];
  property_count: number;
  relationship_count: number;
  instance_count: number;
  evidence_count: number;
  confidence?: number;
  source_fields?: string[];
};
type Instances = {
  total: number;
  offset: number;
  limit: number;
  instances: Array<{
    id: string;
    row_identity: string;
    row_data: Record<string, unknown>;
    evidence_count: number;
  }>;
};

function InstanceRows({
  ontologyId,
  entity,
  onLocate,
}: {
  ontologyId: string;
  entity: EntityType;
  onLocate: () => void;
}) {
  const [offset, setOffset] = useState(0);
  const { data, isLoading } = useQuery({
    queryKey: ["ontology-entity-instances", ontologyId, entity.id, offset],
    queryFn: () =>
      apiClientV2.get<Instances>(
        `/ontologies/${ontologyId}/entities/${entity.id}/instances`,
        { params: { offset, limit: 10 } },
      ),
  });
  const rows = data?.instances || [];
  return (
    <div className="border-t border-slate-100 bg-slate-50/60 px-4 py-3">
      <div className="mb-2 flex items-center justify-between">
        <p className="text-xs text-slate-500">
          真实记录 {data?.total ?? entity.instance_count}
        </p>
        <button
          onClick={onLocate}
          className="inline-flex items-center gap-1 text-xs text-slate-600 hover:text-slate-950"
        >
          在本体中定位 <ExternalLink size={12} />
        </button>
      </div>
      {isLoading ? (
        <div className="flex items-center py-4 text-xs text-slate-500">
          <Loader2 size={13} className="mr-2 animate-spin" />
          读取记录
        </div>
      ) : rows.length ? (
        <div className="overflow-x-auto rounded-lg border border-slate-200 bg-white">
          <table className="min-w-full text-xs">
            <thead className="border-b border-slate-200 bg-slate-50">
              <tr>
                <th className="px-3 py-2 text-left font-medium text-slate-500">
                  行标识
                </th>
                <th className="px-3 py-2 text-left font-medium text-slate-500">
                  字段值
                </th>
                <th className="px-3 py-2 text-left font-medium text-slate-500">
                  证据
                </th>
              </tr>
            </thead>
            <tbody>
              {rows.map((row) => (
                <tr
                  key={row.id}
                  className="border-b border-slate-100 last:border-0"
                >
                  <td className="whitespace-nowrap px-3 py-2 font-mono text-slate-700">
                    {row.row_identity}
                  </td>
                  <td className="max-w-[680px] px-3 py-2 text-slate-600">
                    <div className="flex flex-wrap gap-x-3 gap-y-1">
                      {Object.entries(row.row_data || {})
                        .filter(([key]) => !key.startsWith("_"))
                        .slice(0, 12)
                        .map(([key, value]) => (
                          <span key={key}>
                            <b className="font-medium text-slate-700">{key}</b>{" "}
                            {typeof value === "object"
                              ? JSON.stringify(value)
                              : String(value ?? "—")}
                          </span>
                        ))}
                    </div>
                  </td>
                  <td className="px-3 py-2 text-slate-500">
                    {row.evidence_count}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      ) : (
        <p className="py-3 text-xs text-slate-400">
          该实体类型尚无已发布的真实记录
        </p>
      )}
      {(data?.total || 0) > 10 && (
        <div className="mt-3 flex items-center justify-end gap-2">
          <button
            disabled={offset === 0}
            onClick={() => setOffset((value) => Math.max(0, value - 10))}
            className="rounded border border-slate-200 px-2 py-1 text-xs disabled:opacity-40"
          >
            上一页
          </button>
          <span className="text-xs text-slate-500">
            {Math.floor(offset / 10) + 1} / {Math.ceil((data?.total || 0) / 10)}
          </span>
          <button
            disabled={offset + 10 >= (data?.total || 0)}
            onClick={() => setOffset((value) => value + 10)}
            className="rounded border border-slate-200 px-2 py-1 text-xs disabled:opacity-40"
          >
            下一页
          </button>
        </div>
      )}
    </div>
  );
}

export default function EntitiesTab({ ontologyId }: { ontologyId: string }) {
  const navigate = useNavigate();
  const [expanded, setExpanded] = useState<string | null>(null);
  const { data, isLoading, error } = useQuery({
    queryKey: ["ontology-entities", ontologyId],
    queryFn: () =>
      apiClientV2.get<{
        entities: EntityType[];
        summary: Record<string, number>;
      }>(`/ontologies/${ontologyId}/entities`),
  });
  if (isLoading)
    return (
      <div className="flex min-h-[240px] items-center justify-center text-sm text-slate-500">
        <Loader2 size={16} className="mr-2 animate-spin" />
        正在读取实体
      </div>
    );
  if (error)
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        实体读取失败，请刷新后重试。
      </div>
    );
  const entities = data?.entities || [];
  return (
    <div className="space-y-4">
      <div className="rounded-xl border border-slate-200 bg-white px-4 py-3 text-sm text-slate-600">
        已发布 {entities.length}{" "}
        个实体类型；展开后可查看所属真实记录和来源证据。
      </div>
      <div className="overflow-hidden rounded-xl border border-slate-200 bg-white">
        {entities.map((entity) => {
          const isOpen = expanded === entity.id;
          return (
            <div
              key={entity.id}
              className="border-b border-slate-200 last:border-0"
            >
              <button
                onClick={() => setExpanded(isOpen ? null : entity.id)}
                className="flex w-full items-center gap-3 px-4 py-4 text-left hover:bg-slate-50"
              >
                <span className="text-slate-400">
                  {isOpen ? (
                    <ChevronDown size={17} />
                  ) : (
                    <ChevronRight size={17} />
                  )}
                </span>
                <span className="min-w-0 flex-1">
                  <strong className="block text-sm text-slate-900">
                    {entity.name || entity.name_cn || entity.id}
                  </strong>
                  {entity.name_en && entity.name_en !== entity.name && (
                    <span className="mt-0.5 block text-xs text-slate-500">
                      {entity.name_en}
                    </span>
                  )}
                  <span className="mt-1 block truncate text-xs text-slate-500">
                    {entity.description ||
                      entity.source_fields?.join("、") ||
                      "暂无说明"}
                  </span>
                </span>
                <span className="hidden items-center gap-4 text-xs text-slate-500 md:flex">
                  <span>属性 {entity.property_count}</span>
                  <span>关系 {entity.relationship_count}</span>
                  <span>真实记录 {entity.instance_count}</span>
                  <span>证据 {entity.evidence_count}</span>
                </span>
              </button>
              {isOpen && (
                <InstanceRows
                  ontologyId={ontologyId}
                  entity={entity}
                  onLocate={() =>
                    navigate(
                      `/ontologies/${ontologyId}?tab=data_model&entity_type=${encodeURIComponent(entity.name_en || entity.name || entity.id)}`,
                    )
                  }
                />
              )}
            </div>
          );
        })}
        {!entities.length && (
          <p className="px-4 py-12 text-center text-sm text-slate-400">
            尚未发布实体类型
          </p>
        )}
      </div>
    </div>
  );
}
