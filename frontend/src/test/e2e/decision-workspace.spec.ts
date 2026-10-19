import { execSync } from 'node:child_process'
import { expect, test } from '@playwright/test'

// Runs against a real stack seeded by backend/scripts/seed_material_shortage_demo.py.
// DECISION_E2E_REVIEW_CMD approves as a second account (single-user mode has only one),
// e.g. "docker exec scenario-validation-backend-1 python scripts/review_package_as_second_user.py {ontology} {package} approve".
// Each seeded run can be driven through this flow once; seed again for another run.
const ontology = process.env.DECISION_E2E_ONTOLOGY ?? ''
const study = process.env.DECISION_E2E_STUDY ?? ''
const run = process.env.DECISION_E2E_RUN ?? ''
const reviewCommand = process.env.DECISION_E2E_REVIEW_CMD ?? ''

test.skip(!ontology || !study || !run || !reviewCommand, 'needs a seeded real stack (see comment above)')

test('revenue risk -> root cause -> candidates -> package -> second-person approval -> apply -> revert', async ({ page, request }) => {
  test.setTimeout(120_000)
  await page.goto(`/ontologies/${ontology}/scenario-studies?study=${study}&run=${run}`)
  const workspace = page.getByTestId('material-decision-workspace')
  await expect(workspace).toBeVisible({ timeout: 30_000 })
  const kpis = workspace.locator('.decision-kpis')
  await expect(kpis).toContainText('550,000')
  await expect(kpis).toContainText('11 / 17')
  await expect(kpis).toContainText('CHIP-X')
  const shot = (name: string) => page.screenshot({ path: test.info().outputPath(`${name}.png`), fullPage: true })

  await workspace.getByRole('button', { name: /ORD-1012/ }).click()
  await expect(workspace.locator('.decision-path').first()).toContainText('RF-C')
  await expect(workspace.locator('.decision-path').first()).toContainText('CHIP-X')

  const table = workspace.locator('.candidate-table')
  await expect(table.getByRole('row', { name: /按收入密度重新分配/ })).toContainText('ORD-1003')
  await expect(table.getByRole('row', { name: /认证 Supplier-C/ })).toContainText('supplier_not_qualified')
  await table.getByRole('radio', { name: '加急 + 合格替代 + 跨产线改排' }).check()
  await workspace.getByPlaceholder('为什么选择这个方案，以及接受哪些代价').fill('保护全部 550,000 风险收入，增量成本 3,720，没有受损订单。')
  await workspace.getByRole('button', { name: '创建决策包' }).click()

  const panel = workspace.getByTestId('decision-package')
  await expect(panel).toContainText('草稿')
  const edits = workspace.getByTestId('package-edits')
  await expect(edits).toContainText(/SupplyCommitment (\S+:)?PO-LATE/)
  await expect(edits).toContainText('available_at: 9 → 4')
  await expect(edits).toContainText(/CustomerOrder (\S+:)?ORD-1004/)
  await shot('01-package-preview')

  await panel.getByRole('button', { name: '提交审核' }).click()
  await expect(panel).toContainText('审核中')
  await panel.getByRole('button', { name: '批准' }).click()
  await expect(workspace.getByRole('alert')).toContainText(/cannot review|self_review/)

  const listed = await (await request.get(`/api/v2/ontologies/${ontology}/decision-packages`)).json() as { packages: Array<{ id: string; run_id: string }> }
  const packageId = listed.packages.find(row => row.run_id === run)!.id
  execSync(reviewCommand.replace('{ontology}', ontology).replace('{package}', packageId), { stdio: 'inherit' })

  await page.reload()
  await expect(panel).toContainText('已批准', { timeout: 15_000 })
  await panel.getByRole('button', { name: '执行（合并到本体）' }).click()
  await expect(panel).toContainText('已执行', { timeout: 30_000 })
  await expect(workspace.getByTestId('package-receipts')).toContainText('回执 ERP-')
  await shot('02-applied-with-receipts')

  await page.reload()
  await expect(panel).toContainText('已执行', { timeout: 15_000 })
  await panel.getByRole('button', { name: '回滚本体变更' }).click()
  await expect(panel).toContainText('已回滚', { timeout: 30_000 })
  await expect(panel).toContainText('无法自动撤回')
  await shot('03-reverted')
})
