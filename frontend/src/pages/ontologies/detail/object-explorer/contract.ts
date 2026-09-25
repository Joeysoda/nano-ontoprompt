import type { RuleGroupType, RuleType } from "react-querybuilder";

export type ExplorerMode = "explore" | "results";

export type ExplorerProperty = {
  apiName: string;
  label: string;
  dataType: string;
  values?: Array<{ name: string; label: string }>;
};

export type FilterExpression =
  | { kind: "and" | "or"; items: FilterExpression[] }
  | { kind: "not"; item: FilterExpression }
  | { kind: "comparison"; property: { api_name: string }; op: "eq" | "ne" | "gt" | "gte" | "lt" | "lte" | "in"; value: unknown }
  | { kind: "text"; property: { api_name: string }; mode: "contains" | "starts_with"; query: string; fuzzy: false }
  | { kind: "null_test"; property: { api_name: string }; is_null: boolean }
  | { kind: "array_match"; property: { api_name: string }; mode: "contains_any" | "contains_all"; values: unknown[] }
  | { kind: "interval"; property: { api_name: string }; lower?: unknown; upper?: unknown; lower_inclusive: boolean; upper_inclusive: boolean };

export type ObjectSetExpression =
  | { kind: "base"; type_ref: { kind: "object"; api_name: string } }
  | { kind: "filter"; input: ObjectSetExpression; where: FilterExpression };

export type ExplorerUrlState = {
  objectType: string;
  mode: ExplorerMode;
  query: RuleGroupType;
};

export type CompileResult = {
  expression: ObjectSetExpression;
  issues: string[];
  activeRuleCount: number;
};

export const EMPTY_QUERY: RuleGroupType = { combinator: "and", rules: [] };

function isGroup(node: RuleGroupType | RuleType): node is RuleGroupType {
  return "rules" in node && Array.isArray(node.rules);
}

function coerceScalar(value: unknown, dataType: string): unknown {
  if (typeof value !== "string") return value;
  const raw = value.trim();
  if (["integer", "long"].includes(dataType)) {
    const parsed = Number.parseInt(raw, 10);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (["double", "decimal", "float", "number"].includes(dataType)) {
    const parsed = Number(raw);
    return Number.isFinite(parsed) ? parsed : value;
  }
  if (dataType === "boolean") return raw.toLowerCase() === "true";
  return value;
}

function valueList(value: unknown, dataType: string): unknown[] {
  const values = Array.isArray(value) ? value : String(value ?? "").split(",");
  return values.map((item) => coerceScalar(typeof item === "string" ? item.trim() : item, dataType));
}

function compileRule(rule: RuleType, properties: Map<string, ExplorerProperty>, issues: string[]): FilterExpression | null {
  const field = String(rule.field || "");
  if (!field) return null;
  const property = properties.get(field);
  if (!property) {
    issues.push(`字段 ${field} 不在当前 Object Type 的可查询 metadata 中`);
    return null;
  }
  const ref = { api_name: property.apiName };
  const operator = String(rule.operator || "=");
  const scalar = coerceScalar(rule.value, property.dataType);
  const comparisons: Record<string, "eq" | "ne" | "gt" | "gte" | "lt" | "lte"> = {
    "=": "eq", "!=": "ne", ">": "gt", ">=": "gte", "<": "lt", "<=": "lte",
  };
  if (comparisons[operator]) return { kind: "comparison", property: ref, op: comparisons[operator], value: scalar };
  if (operator === "in" || operator === "notIn") {
    const item: FilterExpression = { kind: "comparison", property: ref, op: "in", value: valueList(rule.value, property.dataType) };
    return operator === "notIn" ? { kind: "not", item } : item;
  }
  if (operator === "contains" || operator === "doesNotContain" || operator === "beginsWith" || operator === "doesNotBeginWith") {
    const item: FilterExpression = {
      kind: "text",
      property: ref,
      mode: operator.includes("begins") || operator.includes("Begin") ? "starts_with" : "contains",
      query: String(rule.value ?? ""),
      fuzzy: false,
    };
    return operator.startsWith("doesNot") ? { kind: "not", item } : item;
  }
  if (operator === "null" || operator === "notNull") return { kind: "null_test", property: ref, is_null: operator === "null" };
  if (operator === "containsAny" || operator === "containsAll") {
    return { kind: "array_match", property: ref, mode: operator === "containsAll" ? "contains_all" : "contains_any", values: valueList(rule.value, property.dataType) };
  }
  if (operator === "between" || operator === "notBetween") {
    const [lower, upper] = valueList(rule.value, property.dataType);
    const item: FilterExpression = { kind: "interval", property: ref, lower, upper, lower_inclusive: true, upper_inclusive: true };
    return operator === "notBetween" ? { kind: "not", item } : item;
  }
  issues.push(`操作符 ${operator} 尚未映射到 Object Set contract`);
  return null;
}

function compileGroup(group: RuleGroupType, properties: Map<string, ExplorerProperty>, issues: string[]): FilterExpression | null {
  const items = group.rules
    .map((node) => isGroup(node) ? compileGroup(node, properties, issues) : compileRule(node, properties, issues))
    .filter((item): item is FilterExpression => item !== null);
  if (!items.length) return null;
  let result: FilterExpression = items.length === 1
    ? items[0]
    : { kind: group.combinator === "or" ? "or" : "and", items };
  if ("not" in group && group.not) result = { kind: "not", item: result };
  return result;
}

export function compileObjectSetExpression(objectType: string, query: RuleGroupType, fields: ExplorerProperty[]): CompileResult {
  const base: ObjectSetExpression = { kind: "base", type_ref: { kind: "object", api_name: objectType } };
  const issues: string[] = [];
  const where = compileGroup(query, new Map(fields.map((field) => [field.apiName, field])), issues);
  return {
    expression: where ? { kind: "filter", input: base, where } : base,
    issues,
    activeRuleCount: where ? countFilters(where) : 0,
  };
}

function countFilters(filter: FilterExpression): number {
  if (filter.kind === "and" || filter.kind === "or") return filter.items.reduce((total, item) => total + countFilters(item), 0);
  if (filter.kind === "not") return countFilters(filter.item);
  return 1;
}

export function readExplorerUrl(params: URLSearchParams): ExplorerUrlState {
  const mode = params.get("view") === "results" ? "results" : "explore";
  const raw = params.get("filters");
  let query = EMPTY_QUERY;
  if (raw) {
    try {
      const parsed = JSON.parse(raw) as RuleGroupType;
      if (parsed && Array.isArray(parsed.rules)) query = parsed;
    } catch {
      query = EMPTY_QUERY;
    }
  }
  return { objectType: params.get("objectType") || "", mode, query };
}

export function writeExplorerUrl(params: URLSearchParams, state: ExplorerUrlState): URLSearchParams {
  const next = new URLSearchParams(params);
  if (state.objectType) next.set("objectType", state.objectType); else next.delete("objectType");
  next.set("view", state.mode);
  if (state.query.rules.length) next.set("filters", JSON.stringify(state.query)); else next.delete("filters");
  next.delete("pageToken");
  return next;
}
