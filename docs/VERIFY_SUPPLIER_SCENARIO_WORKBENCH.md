# Supplier Scenario Workbench 工程验收记录

更新时间：2026-09-21

## 当前已通过

- Docker Compose：PostgreSQL、FalkorDB、Redis、backend、Celery worker、frontend 均可启动。
- Alembic：`upgrade head` 通过；`downgrade 0029_supplier_run_identity` 后再次 `upgrade head` 通过。
- 真实三 Case：Baseline、Supplier B、Supplier C 均产生异步七阶段 Run、独立 result view、metrics、warnings 和 demand-impact artifact。
- Playwright 主 Demo：`supplier_study_full_demo.spec.ts` 通过，完整流程无 console/network 错误且无横向溢出。
- 前端 build：`npm run build` 通过。
- 脚本 lint：`npm run lint:scripts` 通过，0 errors、0 warnings。
- 全量后端回归：`475 passed, 1 skipped, 137 warnings`；此前的 3 个失败已修复并由 targeted regression（37 passed）及全量回归确认。
- 真实浏览器复验：Docker Compose + Chromium 主 Demo 通过，`1 passed (31.8s)`；包含 Compare 的 back/forward URL 恢复。
- 真实故障与重复运行验收：`scripts/verify_supplier_faults.ps1 -RepeatRuns 50` 通过；实际 kill/restart Celery worker、stop/start FalkorDB、失败后 retry、重复 request id 幂等，以及 50 个唯一 Run 全部完成且 output digest 一致。
- Supplier 领域回归：`tests/test_supplier_study_model.py` 通过，包含 golden、typed Action、Logic Asset deterministic execution。

## 实际命令

```powershell
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T backend alembic upgrade head
docker compose -f docker-compose.yml -f docker-compose.local.yml exec -T backend pytest -q tests/test_supplier_study_model.py
docker compose -f docker-compose.yml -f docker-compose.local.yml -f docker-compose.playwright.yml run --rm frontend-playwright npx playwright test src/test/e2e/supplier_study_full_demo.spec.ts --project=chromium --reporter=line
cd frontend
npm run build
npm run lint:src
npm run lint:scripts
cd ..
.\scripts\verify_supplier_faults.ps1 -RepeatRuns 50
```

## 仍未通过的 Gate

- 全仓 `lint:src` 仍有历史页面的 TypeScript `any`、effect 状态更新和 hook 依赖问题，当前最后统计为 138 errors、12 warnings；计划要求 0/0。
- Worker 中断、FalkorDB outage/retry、50 次重复 Run 已由真实 Compose 验收脚本覆盖；完整 back/forward 浏览器恢复已由主 Playwright Demo 覆盖。
- 前端仍未抽出名为 `ScenarioContextProvider` 的单一 context；当前页面已通过 study/case URL 和统一 active Case 逻辑保持主要视图同步。

因此当前状态是“核心业务 Demo 通过，完整工程验收未通过”，不能标记为最终完成。
