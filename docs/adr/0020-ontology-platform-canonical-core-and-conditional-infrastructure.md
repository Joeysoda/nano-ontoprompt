# ADR 0020：Ontology Platform 采用规范核心、可重建投影和阈值触发基础设施

- 状态：Accepted for planning
- 日期：2026-10-18
- 关联计划：`docs/plans/PLAN_ONTOLOGY_PLATFORM_FOUNDATION_GOVERNANCE_RUNTIME.md`
- 关联审计：`docs/plans/PALANTIR_ONTOLOGY_PLATFORM_GAP_AUDIT_2026_10_18.md`

## Context

当前系统已经拥有规范语义资源、revision、权限投影、Schema Migration、Object Query、Action/Function 骨架和 Scenario，但旧 `Entity`/`Relation` 路径、多个数据后端和进程内执行器仍保存竞争语义。用户希望长期追平 Ontology Platform 底层，允许淘汰旧接口，但要求数据迁移、验证和回滚。新增 OpenSearch、Temporal 或外部权限服务会显著增加本机 Docker 和运维成本。

## Decision

1. PostgreSQL 中的 canonical metadata、authoritative instance/edit ledger 和 control state 是唯一事实来源。
2. FalkorDB、未来搜索索引、materialization 和 cache 都是带 checkpoint 的可重建投影。
3. metadata revision、data snapshot、index checkpoint、scenario revision 和 ontology branch head 使用不同身份。
4. 所有对象/链接修改通过 Action transaction contract；Function 只返回 typed result 或 edit batch。
5. Scenario 只隔离对象数据 edits；Ontology Branch/Proposal 独立管理 schema/resource changes。
6. 旧接口可在迁移、shadow verification、两次 dress rehearsal 和可验证 rollback 后删除，不维持永久双栈。
7. OpenSearch、Temporal、OPA/OpenFGA 和 Blueprint 只能在计划定义的 spike/阈值满足后采用；公共 Ontology contract 不依赖这些产品。

## Consequences

- 好处：避免多个后端争夺真相；迁移和回滚可以围绕明确 ledger/revision 执行；UI、Scenario 和未来 AIP 复用同一 contract。
- 成本：需要先完成 inventory、稳定身份映射和旧路径切断，短期内不能优先做大规模 UI 换肤。
- 风险：breaking cutover 会影响现有 demo；通过 shadow dual-read、冻结 revision、回滚包和完整 E2E 控制。
- 后续：每个新基础设施 adoption 都需要单独 ADR，附真实负载、对照基线、许可、运维和退出边界。

## Domain glossary

| 术语 | 本项目中的精确定义 |
|---|---|
| Resource | 具有稳定 `resource_id`、可授权和可版本化的 Ontology schema/logic 定义 |
| Metadata revision | 一组已发布 resource versions 的不可变集合 |
| Data snapshot | authoritative object/link state 在某一读取边界的固定视图 |
| Index checkpoint | 某个投影已经消费到的 source/edit transaction 位置 |
| Projection | 从 canonical metadata 和 ledger 构建、可删除重建的查询表示 |
| Materialization | source data 与 user edits 合并后的当前对象状态输出，不是源真相 |
| Ontology branch | builder 修改 schema/resources 的隔离开发线 |
| Scenario | 业务用户对 object/link edits 的隔离 overlay |
| Action | 授权、校验并原子提交 Ontology edits 的业务写入协议 |
| Function | 隔离、版本化的 typed 计算；不能绕过 Action 直接提交对象修改 |
| Result manifest | 描述 revision、snapshot/checkpoint、permission、adapter、status 和 provenance 的结果信封 |

