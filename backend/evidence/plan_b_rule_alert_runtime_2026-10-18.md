# Plan B 第 7 项：规则运行时与告警生命周期（2026-10-18）

## 结论

规则运行时已经落地，并通过了三层验证：契约测试、真实公开数据的独立答案对照、真实 FalkorDB 读取。规则输入直接复用 Object Set 表达式，过滤块编译回 `FilterObjectSet`，所以成员计算、遍历和数据授权都还是由 Object Query 负责，没有另起一套规则解释器。

## 与 Palantir 公开文档的对应关系

| 本项目 | Palantir 公开契约 | 说明 |
|---|---|---|
| `RuleResource` + `RuleProposal`（open/approved/rejected，带 old/new logic diff） | Foundry Rules object model：Rule 对象和 Proposal 对象 | 采纳 |
| 规则逻辑 = 输入 → 有序逻辑块 → 输出行，输出行带规则的 custom properties | Foundry Rules rule logic | 采纳；目前支持 filter 块和 series_window 块 |
| triggered / recovered / evaluation_failed 历史 | Automate history | 采纳 |
| muted = 照常评估和记录，但不生成建议动作；paused = 停掉定时评估，手动评估仍可用 | Automate muting/pausing | 采纳；定时评估本轮还没做 |
| Alert 有 open/acknowledged/resolved 状态 | Palantir 把 Alert 当作普通对象类型，生命周期由各工作流自定 | **项目扩展** |
| 时间窗口规则 | Foundry Rules 的 time series rules 已经 sunset | **项目扩展**；按点数计算窗口，采样间隔不规则时显式报告 |

文档原文出处：`/docs/foundry/foundry-rules/{overview,core-concepts,object-model,rule-logic}`，`/docs/foundry/automate/{overview,condition-objects,history,muting-pausing-expiration}`。Plan B 第 9 节引用的 `/docs/foundry/rules/overview/` 已经 404。

## 明确写死的语义

- 告警身份是（规则、对象、事件段开始时刻）。同一段越线如果在更新的视图里继续延长，只更新原告警的 `window_end` 和 `occurrence_count`，不会重复开新告警。
- 恢复的判定：输出行消失，或者事件段结束。
- 已解决的告警不会因为同一段事件被重新打开。
- 场景上下文只能预览（返回 422 `scenario_commit_forbidden`）：还没合并的假设性编辑，不能变成线上告警。
- 幂等键 = 规则版本 + 数据视图 + 权限摘要。同一个视图重复提交会返回原来那次评估（`replayed: true`）。失败的评估会记录下来，但不占用幂等键，可以重试。
- 同一时间戳出现两个不同的值时直接失败（`conflicting_points`）；值相同的重复点会合并，并计数报告。
- 提案的 base_version 已经过期时，审批返回 409 `stale_proposal`。非 admin 不能审批自己的提案；admin 自审会记录 `self_approved`。

## 真实数据验证：UCI Steel Industry Energy（CC BY 4.0，sha256 `9b1cee6f…`）

| 断言 | 结果 |
|---|---|
| 规则：每小时（4 个 15 分钟读数）平均用电量 > P95（99.04 kWh） | 独立 pandas 实现算出 330 段；运行时产生的 330 条告警，起止时刻和峰值逐条完全一致 |
| 增量评估：先评估前半段视图，切分点故意落在一段正在越线的事件中间，再评估全量视图 | 第一次 193 段；第二次新增 137 条，跨切分点的那一段被延长且只算 1 次恢复，没有重复告警 |
| 同一视图重复提交 | `replayed: true`，没有新增任何记录 |
| 数据质量 | 35,040 个点，典型间隔 900 秒，不规则间隔 0 |
| 按天过滤规则：周末且日用电量 > 周末中位数（769.04 kWh） | 104 个周末中有 52 天命中，与 pandas 的结果一致 |
| 真实 FalkorDB 读取（backend 镜像，falkordb 1.2.0） | 365 个 EnergyDay 对象写进一次性的图，规则结果与 pandas 一致，测试结束后删除图 |
| 算法性能 | 窗口检测处理 35,040 个点用时 0.062 秒（约 57 万点/秒） |

**数据解释上的发现**：每天 `dd/mm/yyyy 00:00` 那一行（NSM=0）其实是次日 00:00 的读数。它与文件中前后相邻行的中位差只有 0.07 kWh，而与按字面理解时的邻居（当天 00:15）中位差为 0.39 kWh。现有 `uci_steel_energy.py` 是按字面解析的：它的分类汇总不受影响，但 `first_timestamp/last_timestamp` 差了一天，建议修正。

## 测试与门禁

- `tests/test_rule_runtime.py`：24 个用例，覆盖窗口算法与暴力实现对照、乱序和重复点、治理流程、幂等、恢复、场景只读、静音、状态流转、权限隔离。
- `tests/test_rule_runtime_steel.py`：2 个真实数据用例。
- `tests/v2/test_rule_runtime_real_falkor.py`：在容器里与已有的 real-falkor 用例一起跑，7/7 通过。
- 迁移 `0038_rule_alert_runtime`：在一次性 PostgreSQL 16 上 upgrade → downgrade → upgrade 均通过，只有一个 head。
- 前端 `ObjectAlerts`（ObjectPanel 内懒加载）：新增 2 个 e2e，均通过。
- `object-explorer-workflow.spec.ts` 有 6 个用例失败，在不含本改动的基线上同样失败，属于历史问题：用例里找的是 `Actions` 按钮，界面上实际叫"操作"。

## 未做

定时评估（beat）、通知、规则编辑界面、按真实时间长度定义的窗口、事件对象触发器、规则命中后自动提交 Action（按设计不做，只提供预览）。
