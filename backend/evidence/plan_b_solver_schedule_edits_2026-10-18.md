# Plan B 第 8 项：求解器接入 Function → Action 主链（2026-10-18）

## 结论

约束求解器以 edit 类 Function 的形式接进了现有主链：读取固定的 Object Set → 有时间上限的 HiGHS 求解 → 返回求解结果清单和类型化的排程编辑 → 只能经由 Function 支撑的 Action 预览、提交、回滚。不可行、超时、求解器不可用这三种情况不会产生任何编辑，统一返回稳定的错误码 `solver_<status>`。

## 修正的两处已有缺陷

1. **`object_set` 类型的 Function 参数原本无法执行。** `reject_page_binding` 会拒绝任何"带 objects 又带 completeness"的输入，而 `validate_domain` 对 SetHandle 要求的正好是这个形状。现在只放行声明了 `kind: set_handle`、且为完整快照的输入，查询分页结果仍然被拒绝。
2. **SetHandle 里的属性原本由调用方提供，可以伪造。** 新增 `hydrate_domain`：执行前按执行上下文的快照覆盖每个成员的属性，并注入 `ontology_id`。有一个测试专门验证伪造 `hours=0` 不起作用。

## 求解 Function：`operator_assignment_v1`

- 数据来自 frePPLe 公开演示数据（`data/frepple_demo`，固定 revision + sha256，属于**合成/演示数据**）：16 条未关闭需求、3 名操作员及其技能。加工时间 = 所属工序的 `duration + duration_per × 数量`；上漆需要 Varnish skill，只有 Antonio 具备。
- 源数据没有计划期产能（`maximum = 1` 只表示同一时刻只能做一件事），`capacity_hours` 是**显式的场景假设**。
- 模型：广义指派问题（MILP），约束为每条需求恰好分配一人、技能资格、各人产能上限，目标为最小化最大负荷。加了一个极小的确定性平局打破项，保证预览和提交两次求解得到相同的编辑。
- 输出：求解结果清单（status、objective、best_bound、gap、solver_version、time_limit）、`input_digest`、assignments、loads，以及按需求逐条的 `set_property assigned_operator` 编辑（带 expected_old_value）。求解结果清单会随 invoke_action 编辑一起进入 Action 记录。

## 验证

| 场景 | 结果 |
|---|---|
| 基线（每人 120 h） | optimal，makespan 80 h；**与暴力枚举 3¹¹ = 177,147 种分配得到的最优值一致**；负荷 Antonio 80 / Carl 80 / Philippe 70 |
| 订单翻倍（每人 120 h） | `infeasible`：总需求 460 h，超过 3 × 120 h，不产生编辑 |
| 订单翻倍（每人 200 h） | optimal，160 h，与暴力枚举一致 |
| 唯一的上漆工缺席 | `infeasible`（上漆需求没有合格资源），Action 返回 422 `solver_infeasible`，没有任何写入，失败的运行被记录 |
| 真实 FalkorDB + live Action | 预览显示 16 项编辑和求解结果清单，不写入；提交后 SQL 实例与图投影一致；`:revert` 后恢复原状 |

测试：`tests/test_solver_functions.py`（6 个）、`tests/v2/test_solver_action_real_falkor.py`（2 个，在容器内运行）；回归了 Action/Function/Scenario 相关的 39 个用例。

## 未做 / 后续

- `timeout` 和 `feasible`（有可行解但有 gap）两种状态的映射已经实现，但 16 条需求的规模在毫秒级就能求到最优，还没有用真实的大实例触发；需要 S/M 规模的数据。
- Scenario 内的 Function 支撑 Action（让编辑进入 Scenario revision 并参与 compare）还没有做端到端验证。
- 单个 Action 最多 30 项编辑，这是项目已有的限制。大规模排程需要分批提交，或者放宽为有上限的编辑批次，这需要单独决策。
- OR-Tools CP-SAT 对照尚未进行（目前镜像中没有安装）。
