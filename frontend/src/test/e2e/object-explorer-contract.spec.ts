import { expect, test } from "@playwright/test";
import type { RuleGroupType } from "react-querybuilder";

import { compileObjectSetExpression, readExplorerUrl, writeExplorerUrl } from "@/pages/ontologies/detail/object-explorer/contract";

const fields = [
  { apiName: "status", label: "Status", dataType: "string" },
  { apiName: "quantity", label: "Quantity", dataType: "integer" },
];

test("Object Explorer adapter compiles nested filters without leaking query-builder syntax", () => {
  const query: RuleGroupType = {
    combinator: "and",
    rules: [
      { field: "status", operator: "contains", value: "risk" },
      { combinator: "or", not: true, rules: [
        { field: "quantity", operator: ">", value: "10" },
        { field: "quantity", operator: "=", value: "0" },
      ] },
    ],
  };

  const result = compileObjectSetExpression("Order", query, fields);
  expect(result.issues).toEqual([]);
  expect(result.activeRuleCount).toBe(3);
  expect(result.expression).toEqual({
    kind: "filter",
    input: { kind: "base", type_ref: { kind: "object", api_name: "Order" } },
    where: {
      kind: "and",
      items: [
        { kind: "text", property: { api_name: "status" }, mode: "contains", query: "risk", fuzzy: false },
        { kind: "not", item: { kind: "or", items: [
          { kind: "comparison", property: { api_name: "quantity" }, op: "gt", value: 10 },
          { kind: "comparison", property: { api_name: "quantity" }, op: "eq", value: 0 },
        ] } },
      ],
    },
  });
});

test("Object Explorer state survives URL round trip and preserves parent tab", () => {
  const query: RuleGroupType = { combinator: "or", rules: [{ field: "status", operator: "=", value: "open" }] };
  const encoded = writeExplorerUrl(new URLSearchParams("tab=objects"), { objectType: "Order", mode: "results", query });
  expect(encoded.get("tab")).toBe("objects");
  expect(readExplorerUrl(encoded)).toEqual({ objectType: "Order", mode: "results", query });
});

test("Object Explorer rejects fields absent from published query metadata", () => {
  const query: RuleGroupType = { combinator: "and", rules: [{ field: "secret", operator: "=", value: "x" }] };
  const result = compileObjectSetExpression("Order", query, fields);
  expect(result.issues).toEqual(["字段 secret 不在当前 Object Type 的可查询 metadata 中"]);
  expect(result.expression).toEqual({ kind: "base", type_ref: { kind: "object", api_name: "Order" } });
});
