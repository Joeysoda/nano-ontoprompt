import { expect, test } from '@playwright/test'
import fs from 'node:fs'
import path from 'node:path'

type DemoManifest = { ontology_id: string; app_id: string; url: string }

const manifest = JSON.parse(fs.readFileSync(path.resolve('../backend/.factory-twin-demo.json'), 'utf8')) as DemoManifest
const shots = path.resolve('../docs/showcase/factory-twin/screenshots')

async function authenticate(page: import('@playwright/test').Page) {
  const response = await page.request.post('/api/v1/auth/login', { data: { username: 'admin', password: 'admin123' } })
  expect(response.ok()).toBeTruthy()
  const body = await response.json() as { data?: { access_token?: string }; access_token?: string }
  const token = body.data?.access_token ?? body.access_token
  expect(token).toBeTruthy()
  await page.addInitScript(value => {
    localStorage.setItem('token', value)
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: value, user: { username: 'admin', role: 'admin' } }, version: 0 }))
  }, token)
}

test('factory digital twin professional demo is stable and scenario-safe', async ({ page }) => {
  test.setTimeout(120_000)
  await authenticate(page)
  await page.setViewportSize({ width: 1600, height: 1000 })
  await page.goto(manifest.url)
  await expect(page.getByText('Factory Twin Operations', { exact: true })).toBeVisible({ timeout: 30_000 })
  await expect(page.getByTestId('source-status-schedule')).toContainText('13 个对象')
  await page.screenshot({ path: path.join(shots, '01-command-center.png'), fullPage: true })

  await page.getByRole('button', { name: '生产与维护协同排程' }).click()
  const gantt = page.getByTestId('schedule-gantt')
  await expect(gantt).toContainText('12 已排 · 1 未排')
  await expect(gantt.getByLabel('排程层图例')).toContainText('可用产能')
  await expect(gantt.getByLabel('任务依赖箭头')).toBeVisible()
  await expect(gantt.locator('[data-task="wo-100-assemble"]')).toHaveCount(2)
  await page.screenshot({ path: path.join(shots, '02-dynamic-schedule.png'), fullPage: true })

  await gantt.locator('[data-task="wo-200-machine"]').click()
  await expect(gantt.getByText('已选择：WO-200 · Machine bracket')).toBeVisible()
  await page.screenshot({ path: path.join(shots, '03-select-and-reason.png'), fullPage: true })
  await gantt.getByRole('button', { name: '为所选任务查找推荐' }).click()
  await expect(gantt).toContainText('Constraint-aware alternatives', { timeout: 20_000 })
  await page.screenshot({ path: path.join(shots, '04-recommendation.png'), fullPage: true })

  await gantt.getByRole('button', { name: '提前一步' }).click()
  await expect(gantt).toContainText('1 待保存')
  await gantt.getByRole('button', { name: '运行业务校验' }).click()
  await expect(gantt).toContainText('capacity collision', { timeout: 20_000 })
  await page.screenshot({ path: path.join(shots, '05-constraint-block.png'), fullPage: true })
  await gantt.getByRole('button', { name: '撤销' }).click()
  await expect(gantt).toContainText('0 待保存')

  await gantt.locator('[data-task="wo-200-qa"]').click()
  await gantt.getByRole('button', { name: '延后一步' }).click()
  await gantt.getByRole('button', { name: '运行业务校验' }).click()
  await expect(gantt).toContainText('业务校验通过', { timeout: 20_000 })
  await gantt.getByRole('button', { name: '检查 Scenario 变更' }).click()
  await expect(gantt).toContainText('Action 预览：3 项本体修改', { timeout: 20_000 })
  await page.screenshot({ path: path.join(shots, '06-scenario-preview.png'), fullPage: true })

  await page.reload()
  await expect(page.getByTestId('schedule-gantt')).toContainText('0 待保存')
  await page.getByRole('button', { name: '设备数字孪生' }).click()
  await expect(page.getByText('NASA C-MAPSS 设备健康快照')).toBeVisible()
  await expect(page.getByRole('cell', { name: 'Turbofan EQ005' })).toBeVisible()
  await page.screenshot({ path: path.join(shots, '07-asset-twin.png'), fullPage: true })

  await page.getByRole('link', { name: '编辑' }).click()
  await expect(page.getByTestId('workshop-editor')).toBeVisible()
  await page.getByRole('tab', { name: '生产与维护协同排程' }).click()
  await page.getByRole('button', { name: '编辑组件 Factory Twin · Dynamic Schedule' }).click()
  await expect(page.getByText('排程层字段（可选）')).toBeVisible()
  await expect(page.getByText('推荐推理能力')).toBeVisible()
  await page.screenshot({ path: path.join(shots, '08-generic-builder.png'), fullPage: true })
})
