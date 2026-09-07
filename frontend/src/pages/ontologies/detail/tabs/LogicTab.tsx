import { useMemo, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Loader2, Search } from "lucide-react";
import { apiClientV2 } from "@/api/client";

type Rule = {
  id: string;
  name: string;
  name_cn?: string;
  name_en?: string;
  description?: string;
  formula?: string;
  condition?: unknown;
  effect?: unknown;
  linked_entities?: string[];
  evidence?: Record<string, unknown>;
  confidence?: number;
  model_invocation_id?: string | null;
};

function printed(value: unknown) {
  if (
    value == null ||
    value === "" ||
    (typeof value === "object" && !Object.keys(value as object).length)
  )
    return "—";
  return typeof value === "string" ? value : JSON.stringify(value, null, 2);
}

export default function LogicTab({ ontologyId }: { ontologyId: string }) {
  const [query, setQuery] = useState("");
  const { data, isLoading, error } = useQuery({
    queryKey: ["ontology-logic-rules", ontologyId],
    queryFn: () =>
      apiClientV2.get<{ logic_rules: Rule[] }>(
        `/ontologies/${ontologyId}/graph`,
        { params: { view: "ontology" } },
      ),
  });
  const rules = useMemo(() => {
    const needle = query.trim().toLowerCase();
    const all = data?.logic_rules || [];
    if (!needle) return all;
    return all.filter((rule) =>
      [
        rule.name,
        rule.name_cn,
        rule.name_en,
        rule.description,
        rule.formula,
        printed(rule.condition),
        printed(rule.effect),
      ].some((value) =>
        String(value || "")
          .toLowerCase()
          .includes(needle),
      ),
    );
  }, [data, query]);
  if (isLoading)
    return (
      <div className="flex min-h-[240px] items-center justify-center text-sm text-slate-500">
        <Loader2 size={16} className="mr-2 animate-spin" />
        正在读取逻辑规则
      </div>
    );
  if (error)
    return (
      <div className="rounded-xl border border-red-200 bg-red-50 p-4 text-sm text-red-700">
        逻辑规则读取失败，请刷新后重试。
      </div>
    );
  return (
    <div className="space-y-4">
      <div className="relative max-w-md">
        <Search
          size={15}
          className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-slate-400"
        />
        <input
          value={query}
          onChange={(event) => setQuery(event.target.value)}
          className="w-full rounded-lg border border-slate-300 py-2 pl-9 pr-3 text-sm outline-none focus:border-slate-700"
          placeholder="搜索规则名称、条件或结果"
        />
      </div>
      <div className="space-y-3">
        {rules.map((rule) => (
          <article
            key={rule.id}
            className="rounded-xl border border-slate-200 bg-white p-5"
          >
            <div className="flex flex-wrap items-start justify-between gap-3">
              <div>
                <h3 className="text-base font-semibold text-slate-900">
                  {rule.name_cn || rule.name}
                </h3>
                {rule.name_en && (
                  <p className="mt-1 text-xs text-slate-500">{rule.name_en}</p>
                )}
                {rule.description && (
                  <p className="mt-2 text-sm text-slate-600">
                    {rule.description}
                  </p>
                )}
              </div>
              <span className="rounded bg-slate-100 px-2 py-1 text-xs text-slate-600">
                置信度 {Number(rule.confidence ?? 1).toFixed(2)}
              </span>
            </div>
            <div className="mt-4 grid gap-3 md:grid-cols-2">
              <section className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  IF
                </p>
                <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-5 text-slate-700">
                  {printed(rule.condition)}
                </pre>
              </section>
              <section className="rounded-lg border border-slate-200 bg-slate-50 p-3">
                <p className="mb-2 text-[11px] font-semibold uppercase tracking-wide text-slate-500">
                  THEN
                </p>
                <pre className="whitespace-pre-wrap break-words font-sans text-xs leading-5 text-slate-700">
                  {printed(rule.effect)}
                </pre>
              </section>
            </div>
            {rule.formula && (
              <div className="mt-3 rounded-lg border border-slate-200 px-3 py-2 font-mono text-xs text-slate-600">
                {rule.formula}
              </div>
            )}
            <div className="mt-4 grid gap-3 text-xs text-slate-600 md:grid-cols-2">
              <div>
                <span className="font-medium text-slate-800">关联实体：</span>
                {rule.linked_entities?.length
                  ? rule.linked_entities.join("、")
                  : "—"}
              </div>
              <div>
                <span className="font-medium text-slate-800">来源证据：</span>
                {rule.evidence && Object.keys(rule.evidence).length
                  ? printed(rule.evidence)
                  : "—"}
              </div>
            </div>
          </article>
        ))}
        {!rules.length && (
          <div className="rounded-xl border border-slate-200 bg-white py-12 text-center text-sm text-slate-400">
            {query ? "无匹配规则" : "尚未发布逻辑规则"}
          </div>
        )}
      </div>
    </div>
  );
}
