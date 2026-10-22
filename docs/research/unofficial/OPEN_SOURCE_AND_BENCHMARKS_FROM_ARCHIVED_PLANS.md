# 已归档计划里引用的开源项目与公开数据集

> 提取日期：2026-10-22。来源计划在 `docs/archive/`。证据等级：第三方（项目官方页面/仓库）；"用途"一列是当时的**本项目推断**。

| 项目 / 数据集 | 是什么 | 当时的用途或结论 | 来源计划 |
| --- | --- | --- | --- |
| [frePPLe](https://github.com/frePPLe/frepple) | 开源供应链计划软件，自带制造业样例数据 | 做 Supplier Study 的固定测试数据：commit `73e5be3d1573db043209111325dd921d68cf4b88`，fixture SHA-256 `d7eb98078882b234c395fd053c5f6fbda33810cb90add2adb4bf7d62f28637ef`；384 条源记录、363 个业务对象、903 条关系、220 个 Demand（16 个 open）。样例本身是合成数据；供应商 B/C 是项目自己加的合成假设，要标明 synthetic。成本单位没有声明，不能说成美元。 | `PLAN_SCENARIO_CONTEXT_PROVIDER.md`、`DEMO_SUPPLIER_SCENARIO_WORKBENCH.md` |
| [LUBM](https://swat.cse.lehigh.edu/projects/lubm/) | Lehigh 大学的本体/语义查询基准，可按规模重复生成数据，带 14 个标准查询 | 规划的压力测试：1万 / 10万 / 100万及以上规模，并发 1/5/20/50，记录吞吐、P50/P95/P99、失败率、内存、恢复时间。从未执行。 | `大规模高并发架构方案.md`、`验收记录.md` |
| [NYC TLC Trip Record Data](https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page) | 纽约出租车行程公开数据 | 建议用来验证跨表关系、行/字段级安全和增量索引；要记录下载 URL、获取日期、checksum、记录数和许可证。从未采用。 | `PLAN_PERSON_A_SEMANTIC_CORE_SECURITY_DATA_PLANE.md` |
