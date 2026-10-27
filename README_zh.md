# OntoAct

面向制造缺料问题的决策工作台：追踪受影响订单、比较可行方案，并在另一位用户审核后应用变更，保留审计与回滚记录。

[English](README.md) · [演示步骤](docs/showcase/DEMO.md) · [工程取舍](docs/showcase/ENGINEERING.md) · [验证记录](docs/showcase/VERIFICATION.md)

一个延期零件可能影响多个产品，并让订单争抢同一份库存。OntoAct 将订单、BOM、库存、供应承诺和产线能力连接起来，对每个候选方案重新运行同一个确定性模拟器，再把选定方案编译为可审核的具体对象变更。

## 可以验证的结果

主案例使用合成数据，包含 12 个订单、多层 BOM、预留库存、过期供应承诺和两条产线。基线风险收入为 **550,000**。

| 方案 | 剩余风险收入 | 增量成本 | 代价或约束 |
|---|---:|---:|---|
| 加急延期供货 | 140,000 | 3,200 | 恢复三个订单，仍有一个缺料 |
| 采购合格替代料 | 480,000 | 520 | 恢复一个订单 |
| 重分配库存 | 420,000 | 0 | 两个订单获益，另外两个受损 |
| 使用未认证供应商 | 140,000 | 880 | 被供应商资质硬约束拒绝 |
| 加急 + 合格替代 + 跨线改排 | 0 | 3,720 | 四个风险订单全部恢复 |

这些数字是[手工推导的样例期望值](backend/unit_tests/test_l3harris_known_answer.py)，不是客户节省金额或生产效果。算法是有界启发式，不宣称全局最优。ERP 使用本地模拟连接器。

## 启动完整决策演示

需要 Git、Docker Compose 2.24.4+ 和 Bash；Windows 使用 WSL 或 Git Bash。干净机器上的资源需求与首次启动耗时尚未测量。

```bash
bash scripts/bootstrap_deployment.sh --seed-decision-demo
```

命令为本地服务启用 JWT，创建独立的作者/审核者演示账号，准备对象驱动的缺料研究并等待基线计算，核对已知答案。链接和随机账号只写入本机私有文件 `.decision-demo.json`，不进入 Git。每次执行创建新的演示，不重置其他本体。

1. 本地打开 `.decision-demo.json`，以 author 登录，再打开其中的 url。
2. 查看风险订单和物料路径，比较方案及受损订单。
3. 选择组合方案，核对具体 edits，创建并提交决策包。
4. 退出登录，使用 reviewer 登录，回到相同链接并批准。
5. 应用后刷新确认结果与回执，再回滚。外部通知不能通过恢复对象状态自动撤回。

这条流程不需要模型 API key。可选 AI 解释需要 `OPENAI_API_KEY`；解释正文及摘要均未做语义核验，数值和引用匹配不代表事实正确。

## 建议看这几处实现

- [分配与候选重算](backend/app/services/v2/material_shortage.py)：共享物料不重复分配，失败订单释放暂存物料。
- [独立计算测试](backend/unit_tests/test_material_shortage_decision.py)：已知答案、反例与业务不变量。
- [决策包状态机](backend/app/services/v2/decision_packages.py)与[接口测试](backend/tests/test_material_decision_package_flow.py)：拒绝自审、ETag、幂等与并发审核。
- [真实 FalkorDB 应用/回滚测试](backend/tests/v2/test_material_decision_apply_real_falkor.py)：应用后重算应复现预测。
- [模型解释边界](backend/app/services/v2/decision_explanations.py)：模型提供评论，不批准或直接提交变更。

## 范围与验证

React/TypeScript 展示对象与方案；FastAPI 提供契约与操作入口；PostgreSQL 保存事实和执行记录；FalkorDB 提供图投影；Celery/Redis 执行后台任务；用户 Function 通过独立容器运行。

目前仍是开发/演示交付：遗留接口未全部迁移统一授权，默认 Compose 仍包含主流程不需要的服务。旧自然语言 Cypher 执行接口已返回 410，避免未经治理的模型查询直接执行。Function runner 的内部网络仍与 backend/worker 相连，不作为敌对多租户代码执行服务。

```bash
cd backend
pip install -r requirements.txt
python -m pytest unit_tests -q

cd ../frontend
npm ci
npm run lint:src
npm run build
```

使用 Python 3.12、Node.js 22 和独立 Python 环境。[CI](.github/workflows/verify.yml) 分开运行单元、前端及真实服务集成检查；[验证记录](docs/showcase/VERIFICATION.md) 明确区分已执行与待验证，不把定义了工作流当成通过。

更广的平台能力和原有部署资料见[平台参考](docs/showcase/PLATFORM_REFERENCE_ZH.md)与[部署指南](DEPLOYMENT_WORKBENCH_ZH.md)。开发使用 AI 辅助；质量依据是可审查的边界、独立期望值和失败测试，不以生成的文字作为验证。

[MIT](LICENSE)。第三方数据及参考材料遵守各自许可。
