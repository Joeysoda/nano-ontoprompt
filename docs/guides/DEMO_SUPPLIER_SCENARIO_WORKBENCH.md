# Supplier Study — 逐步 Demo 操作稿

前提：PostgreSQL、FalkorDB、Redis/Celery、前后端已启动；固定 frePPLe fixture、Baseline、Supplier B/C 的 synthetic profile 已准备好。此操作稿从打开系统开始，不含导入或现场建模。截图统一保存至 `frontend/test-results/supplier-demo/`。数据是官方 frePPLe 合成制造样例；B/C 是本项目明确标记的 synthetic 假设，不是生产观测或 Palantir 官方供应商数据。成本单位未由 fixture 声明，不能说成美元。

1. 打开 `http://127.0.0.1:5173`，从 sidebar 点击 **What-if / Scenarios**。预期自动打开 **Wood Supply Resilience**，同一工作面左边是 system graph，右边是 Simulation，默认选中 Supplier B；不输入 ontology ID 或 view ID。讲解三个 Case 共用固定基线，B/C revision 互相隔离。截图 `01-open.png`。
2. 在右侧确认 **Time** 为 2021-01-01、**Scope** 为 Objects on Graph（wooden beam/panel），展开 **Advanced options** 确认 smoothing 0、Run baseline sim On；确认 Models 只读 provenance `supplier_resilience_v2` / `2.1.0` / `bounded_wood_supply`。这里的参数随 Run 冻结。截图 `02-options.png`。
3. 在 Supplier B 行点 **Add action / Edit overrides**，确认 `Applying to Supplier B` 和粉色参数单元格；可查看 lead days、MOQ、order multiple、capacity、cost multiplier，然后点 **Submit Action**。预期 Summary 从无变更变为 Action → ChangeSet → revision；Graph 同步切为 B revision；Run 绝不使用未提交草稿。截图 `03-b-action.png`。
4. 点 Supplier B 的 **Run**。接口应立即返回 202/queued，自动创建或复用兼容 Baseline，再执行 B。打开 **Run details**，观察七个真实阶段依次记录：Validate Action、Apply ChangeSet、Bind scenario data、Run supply model、Calculate impact、Materialize results、Finalize；每个阶段有持久状态和耗时，最终绿色完成。讲解 Celery 异步执行，不是页面动画。运行时截图 `04-b-running.png`，完成后截图 `05-b-stages.png`。
5. 点 **Summary**：确认 B 的 on-time delivery、late demands、wood shortage、estimated procurement cost、告警和完成耗时；模型在 16 条未关闭需求上展开 BOM、扣库存，再按交期/优先级分配采购。点击 graph 节点检查 Selection Card 的当前 Case 和对象属性；点 **Impacts** 查看 Demand 明细、客户过滤并选中一行。截图 `06-b-summary.png`、`07-b-impacts.png`。
6. 切到 Supplier C，重复 **Add action / Edit overrides → Submit Action → Run**。确认 C 有独立 ChangeSet/revision，左侧 Graph、Selection Card、Summary、需求表同时切至 C；七阶段完成。截图 `08-c-action.png`、`09-c-stages.png`。
7. 点 **Compare**，确认它展示的是相对 Baseline 的差异、方向条和指标级排名；绝对输入/输出仍以右侧 Models 三列为准，避免重复表格。点击 B/C 的差异条可进入对应 Case 的 Demand impacts。固定 profile 预期：Baseline 准时率 62.5%、估算采购成本 22500 cost units；B 43.75%、10260；C 68.75%、14100。成本按 MOQ/倍数后的全部计划采购量计算；B 容量不足导致缺货，C 比 B 交期更好但成本更高，原供应商的 panel MOQ 又使 Baseline 最贵。不宣称自动最佳选择。截图 `10-compare.png`。
8. 点 **Evidence**，核对当前 C 的 Action、ChangeSet、revision、输入/输出 digest、fixture manifest、result view 和 model 版本。切回 B 再切 C，确认没有混合上下文。截图 `11-evidence.png`。
9. 保持 C 的 URL，刷新浏览器，再用浏览器后退/前进恢复活动 Case；确认 Compare、七阶段和对象明细仍可读。重启 backend、worker、FalkorDB 后再次刷新，确认 PostgreSQL Run 状态及 FalkorDB 图仍存在，Compare 数值不变。截图 `12-recovery.png`。

浏览器自动化必须按上述同一顺序从第一步完整执行，不得省略失败步骤。若出现 console/network 错误、布局溢出、业务结果不符或截图明显偏离参考，修复后从第 1 步重新跑。
