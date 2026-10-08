import { test, expect } from '@playwright/test'

test('supplier scenario workbench runs the prepared study and restores results', async ({ page }) => {
  await page.goto('/login')
  await page.getByPlaceholder('用户名').fill('admin')
  await page.getByPlaceholder('密码').fill('admin123')
  await page.getByRole('button', { name: '登录', exact: true }).click()
  await expect(page).toHaveURL(/\/overview$/)

  await page.getByRole('link', { name: 'What-if / Scenarios' }).click()
  await expect(page).toHaveURL(/\/ontologies\/[^/]+\/what-if\?scenario=.*tab=Summary/)
  await expect(page.getByRole('heading', { name: 'Scenario Summary' })).toBeVisible()
  await expect(page.getByText('Time', { exact: true })).toBeVisible()
  await expect(page.getByText('Scope', { exact: true })).toBeVisible()

  await page.getByTestId('run-supplier_disruption_impact_v1').click()
  await expect(page.getByTestId('run-workflow')).toContainText('queued', { timeout: 5000 })
  await expect(page.getByTestId('run-workflow')).toContainText('completed', { timeout: 15000 })
  await expect(page.getByTestId('run-workflow')).toContainText('validate action')
  await expect(page.getByTestId('run-workflow')).toContainText('finalize')
  await expect(page.getByText('Total cost', { exact: true })).toBeVisible()
  await expect(page.getByText('Shortage quantity', { exact: true })).toBeVisible()

  await page.screenshot({ path: 'test-results/supplier-scenario-summary.png', fullPage: true })
  await page.reload()
  await expect(page.getByRole('heading', { name: 'Scenario Summary' })).toBeVisible()
  await expect(page.getByTestId('run-workflow')).toContainText('completed', { timeout: 10000 })
})
