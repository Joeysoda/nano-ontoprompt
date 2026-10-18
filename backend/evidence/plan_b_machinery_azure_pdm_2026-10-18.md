# Plan B 第 9 项：Machinery 维护链路验收（Azure PdM 全量，2026-10-18）

## 结论

在同一个设备对象上打通了 Machinery 用例的共用运行时：Time Series Property、Event Object、规则告警、维护 Action，以及从告警到 Action 运行记录的追溯链。验收用的是 Azure PdM 全部 100 台设备，读写走真实 FalkorDB。没有做完整的 Machinery 产品界面，这符合计划里的停止线。

**数据性质**：Azure PdM 是微软发布的**模拟**预测性维护样例，不是现场观测数据，下面的数字只能说明本运行时的行为，不能当作真实设备的效果。源时间戳不带时区，按 UTC 处理。数据（80 MB）放在仓库外的 `<workspace>/data/azure_pdm`，各文件的 sha256 登记在 `tests/data/azure_pdm_manifest.json`。

## 映射（对照 Palantir 的公开资源模型）

| 源数据 | 本体表面 |
|---|---|
| `PdM_machines.csv`（100 行） | `Machine` 对象：model、age、maintenance_status，外加 4 个 TSP 属性 `*_series` |
| `PdM_telemetry.csv`（876,100 行 × 4 个通道） | 400 条 `TimeSeriesSync`，每条 8,761 个点。**读数是 TSP 上的点，不是对象** |
| failures / maint / errors（761 / 3,286 / 3,919 行） | `FailureEvent` / `MaintenanceEvent` / `ErrorEvent` 三类事件对象，带 start/end timestamp，分别通过 `FAILURE_OF` / `MAINTENANCE_OF` / `ERROR_OF` 链接到设备 |

适配器 `services/v2/azure_pdm.py` 是演示/验收用的批量适配器，产出与 `temporal_surface.install_surface` 相同的契约，不是通用摄入 API（不变量 12）。

## 验收结果（`tests/v2/test_machinery_azure_pdm.py`，在容器内对真实 FalkorDB 运行，耗时 42 秒）

| 断言 | 结果 |
|---|---|
| 对象、事件、序列、点数与 CSV 对账 | 100 / 7,966 / 400 / 3,504,400，全部一致 |
| TSP 能通过现有时序接口读取 | `machine-001` 的 vibration 单位为 mm/s，首个点 2015-01-01T06:00Z |
| 事件对象能被 Object Query 查询 | 过滤 `code == comp4` 得到 179 条，与 CSV 一致；FailureEvent 沿 `FAILURE_OF` 遍历到正确的设备 |
| ObjectPanel"关联事件"发出的请求（反向遍历 + 按时间倒序） | 与 pandas 排序结果逐条一致 |
| 四条通道规则（24 小时均值 ±3σ，阈值完全由数据分布决定，不使用故障标签） | 告警与独立 pandas 实现逐条一致 |
| 维护 Action 闭环 | 告警的建议请求 → 预览（1 项编辑）→ 提交 → 带 `action_run_id` 解决告警；SQL 实例的 `maintenance_status` 变为 `scheduled`；用另一台设备的 Action 运行记录去解决会被拒绝（409）|
| 追溯 | 告警历史依次为 triggered → … → acknowledged → resolved（含 action_run_id）；evidence 里带 series_id |

### 规则的预测价值（48 小时提前窗口，对比同一设备上随机时间点的基线）

| 通道 → 部件 | 阈值 | 告警段数 | 故障数 | 召回率 | 随机基线 | 精确率* |
|---|---:|---:|---:|---:|---:|---:|
| volt ↑ → comp1 | 184.923 | 976 | 192 | 0.995 | 0.104 | 0.278 |
| rotate ↓ → comp2 | 392.397 | 886 | 259 | 1.000 | 0.116 | 0.342 |
| pressure ↑ → comp3 | 115.054 | 639 | 131 | 1.000 | 0.145 | 0.214 |
| vibration ↑ → comp4 | 46.563 | 687 | 179 | 1.000 | 0.115 | 0.278 |

\* 精确率 = 告警段开始后 48 小时内发生对应部件故障的比例，来自探索性分析脚本，测试里没有断言它。约 2/3 的告警是误报。召回率接近 100%，很可能是因为模拟数据在故障前注入了异常，**不能外推到真实设备**。测试只断言召回率 ≥ 3 倍随机基线。

## 已知限制

- 设备对象同时写在 SQL `EntityInstance`（供 Action 写入）和 FalkorDB live 图（供查询）里，这是仓库里已有的双真相源问题（Plan A 范畴），验收按现状如实使用。
- TSP 的点以 JSON 存在 `TimeSeriesSync` 一行里（每条约 8.8k 个点）。对于更高频的数据，需要专门的时序存储。
- 流式摄入管线（TemporalReplay/StreamEvent）目前会把每条事件都物化成事件对象，不适合遥测数据；后续应让它支持"只生成 TSP 点"的模式。
