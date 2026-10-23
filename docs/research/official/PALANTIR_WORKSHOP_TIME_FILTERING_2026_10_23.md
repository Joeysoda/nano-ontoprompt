# Palantir：Workshop 里时间筛选与汇总指标的建模方式

> 日期：2026-10-23 · 来源：Palantir 官方文档（下列链接，2026-10-23 读取）及本地 PDF `evidence/palantir-docs/palantir_derived_properties.pdf` · 证据等级：官方文档；“对我们的含义”一节为本项目推断
> 起因：Workshop 城市页切换“最近 7 天”没有变化——门店城市是预先汇总的对象，没有时间字段。

## 一句话结论

Palantir 不预存“城市 × 日期”这类汇总对象：带时间的是**事件粒度的对象**（如订单），时间筛选作用在事件对象集上，城市等维度上的指标在**查询时**聚合出来，再通过 Search Around 或派生属性回到城市对象。

## 官方文档怎么说

- **时间筛选是对象集上的属性筛选**（[Filter List](https://www.palantir.com/docs/foundry/workshop/widgets-filter-list)）：日期属性可用单/多日期选择器、时间轴展示筛选；组件输出一个“对象集筛选变量”，下游对象集变量套用它，绑定这些变量的组件随之变化。属性 ID 相同时，同一个筛选还能作用到其他对象类型的对象集。页面没有写相对时间范围（“最近 7 天”）是否内置。
- **对象集变量可以从另一个对象集加筛选得到，也能沿关系跳转**（[Variables](https://www.palantir.com/docs/foundry/workshop/concepts-variables)）：“defined by selected object types, filters, and linked objects traversals”；可以 Search Around 到关联对象；“Object set aggregation” 变量由对象集聚合得到，日期/数值变量也能来自聚合。
- **派生属性在查询时计算**（[Derived properties](https://www.palantir.com/docs/foundry/ontology/derived-properties/)，Beta）：基于其他属性或关联对象计算，“includes aggregating on or selecting properties of linked objects”，算出后可在同一请求里继续筛选、排序、聚合；权限继承所有参与计算的对象。
- **连续测量值用时间序列属性**（[Time series properties](https://www.palantir.com/docs/foundry/time-series/time-series-properties)）：对象上挂一条历史时间序列；只有部分对象有的测量值放到单独的传感器对象类型。有开始/结束时间的事件用 [Timeline](https://www.palantir.com/docs/foundry/workshop/widgets-timeline) 按时间展示对象。

## 对我们的含义（本项目推断）

| 现状 | 按官方思路应改为 |
|---|---|
| 门店城市对象里存好汇总值（订单数、销售额、迟交率），没有时间 | 订单是带下单日期的对象并链接到城市；城市指标按所选时间范围在查询时聚合 |
| 时间范围在浏览器里过滤已经取回的行（最多 1 万行） | 时间范围作为对象集筛选条件下推到后端，聚合在后端完成（DataCo 有 65,752 张订单，超过 1 万） |
| 时间字段写在每个数据源上 | 可保留；另外支持一个筛选作用到多个对象类型（按属性名匹配） |

## 不该照搬 / 存疑

- 派生属性在 Palantir 仍是 Beta；我们已有 `object-query/aggregate`（分组、时间分桶、按快照精确聚合），先用它，不急着做通用派生属性。
- “最近 N 天”的锚点：官方文档没写相对时间范围；社区帖子提到“相对今天”，但我们的演示数据是历史数据，继续以数据最新时间为锚点，界面上写明。
