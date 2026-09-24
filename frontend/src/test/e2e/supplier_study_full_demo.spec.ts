import { test, expect } from '@playwright/test'
import { mkdir } from 'node:fs/promises'
import { join } from 'node:path'

const output = join(process.cwd(), 'test-results', 'supplier-demo')

test('complete prepared Supplier Study demo, in the documented order', async ({ page }) => {
  test.setTimeout(120_000)
  await mkdir(output, { recursive: true })
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  page.on('console', message => { if (message.type() === 'error') errors.push(message.text()) })
  page.on('response', response => { if (response.url().includes('/api/') && response.status() >= 400) errors.push(`${response.status()} ${response.url()}`) })
  const shot = async (name: string) => page.screenshot({ path: join(output, name), fullPage: true })

  // 1. First-time business-user entry through the sidebar.
  await page.goto('/')
  await page.getByRole('link', { name: 'What-if / Scenarios' }).click()
  await expect(page.getByTestId('supplier-study')).toBeVisible({ timeout: 30000 })
  await expect(page.getByText('Wood Supply Resilience').first()).toBeVisible()
  await expect(page.getByTestId('case-supplier_b')).toBeVisible()
  await expect(page.getByTestId('system-graph')).toBeVisible()
  await shot('01-open.png')

  // 2. Pinned time, scope and model provenance.
  await expect(page.getByLabel('Time')).toHaveValue(/2021-01-01/)
  await expect(page.getByText('Objects on Graph')).toBeVisible()
  await page.getByText('Advanced options').click()
  await expect(page.getByLabel('Run baseline sim')).toBeChecked()
  await expect(page.getByText('supplier_resilience_v2')).toBeVisible()
  await shot('02-options.png')

  // 3. Submit the typed Supplier B Action and inspect its revision.
  await page.getByTestId('add-action').click()
  await expect(page.getByText(/Applying to Supplier B/)).toBeVisible()
  await expect(page.getByLabel('lead_days wooden beam')).toHaveValue('4')
  await shot('03-b-action.png')
  const bSubmit = page.waitForResponse(response => response.url().includes('/cases/') && response.request().method() === 'PATCH')
  await page.getByTestId('submit-action').click()
  const bSubmitted = await bSubmit
  expect(bSubmitted.status()).toBe(200)
  const bSubmittedRevision = (await bSubmitted.json()).revision
  await expect(page.getByTestId('case-supplier_b')).toContainText(`rev ${bSubmittedRevision}`)
  await expect(page.getByTestId('submit-action')).toBeDisabled()
  await expect(page.getByText(/ChangeSet/).first()).toBeVisible()

  // 4. True asynchronous run with seven persisted stages.
  const bResponse = page.waitForResponse(response => response.url().includes('/cases/') && response.url().endsWith('/runs') && response.request().method() === 'POST')
  const bRevision = Number((await page.getByTestId('case-supplier_b').innerText()).match(/rev (\d+)/)?.[1])
  await page.getByTestId('run-active-case').click()
  const bQueued = await bResponse
  expect(bQueued.status()).toBe(202)
  expect((await bQueued.json()).revision).toBe(bRevision)
  await expect(page.getByRole('tab', { name: 'Run details' })).toHaveAttribute('aria-selected', 'true')
  await shot('04-b-running.png')
  await expect(page.getByText(/^completed · 100%/)).toBeVisible({ timeout: 30000 })
  await expect(page.getByTestId('workflow-stages').locator('div')).toHaveCount(7)
  await expect(page.getByTestId('workflow-stages').getByText('Finalize')).toBeVisible()
  await expect(page.getByTestId('workflow-stages').getByText('completed', { exact: true })).toHaveCount(7)
  const location = new URL(page.url())
  const ontologyId = location.pathname.split('/')[2]
  const studyId = location.searchParams.get('study')
  const studyResponse = await page.request.get(`/api/v2/ontologies/${ontologyId}/scenario-studies/${studyId}`)
  expect(studyResponse.ok()).toBe(true)
  const studySnapshot = await studyResponse.json()
  expect(studySnapshot.cases.find((item: { key: string }) => item.key === 'baseline').runs[0].status).toBe('completed')
  expect(studySnapshot.cases.find((item: { key: string }) => item.key === 'supplier_b').runs[0].result_view_id).toBeTruthy()
  await shot('05-b-stages.png')

  // 5. Summary, graph selection and demand details.
  await page.getByRole('tab', { name: 'Summary' }).click()
  await expect(page.getByText('43.75').first()).toBeVisible()
  await shot('06-b-summary.png')
  await page.getByRole('tab', { name: 'Impacts' }).click()
  await expect(page.getByTestId('demand-impacts').locator('tbody tr')).toHaveCount(16)
  await page.getByLabel('Filter demand or customer').fill('shop')
  await page.getByLabel('Filter demand or customer').fill('')
  await page.getByTestId('demand-impacts').locator('tbody tr').first().click()
  await expect(page.getByTestId('selection-card').getByText('Supplier B')).toBeVisible()
  await page.getByTestId('selection-card').getByRole('button', { name: 'Model outputs' }).click()
  await expect(page.getByTestId('selection-card').getByText('Late days')).toBeVisible()
  await page.getByTestId('selection-card').getByRole('button', { name: 'Warnings' }).click()
  await expect(page.getByTestId('selection-card').getByText(/source-defined/)).toBeVisible()
  await shot('07-b-impacts.png')

  // 6. Independent Supplier C Action/revision/run.
  await page.getByTestId('case-supplier_c').click()
  await page.getByTestId('add-action').click()
  await expect(page.getByText(/Applying to Supplier C/)).toBeVisible()
  await shot('08-c-action.png')
  const cSubmit = page.waitForResponse(response => response.url().includes('/cases/') && response.request().method() === 'PATCH')
  await page.getByTestId('submit-action').click()
  const cSubmitted = await cSubmit
  expect(cSubmitted.status()).toBe(200)
  const cSubmittedRevision = (await cSubmitted.json()).revision
  await expect(page.getByTestId('case-supplier_c')).toContainText(`rev ${cSubmittedRevision}`)
  await expect(page.getByTestId('submit-action')).toBeDisabled()
  const cResponse = page.waitForResponse(response => response.url().includes('/cases/') && response.url().endsWith('/runs') && response.request().method() === 'POST')
  await page.getByTestId('run-active-case').click()
  expect((await cResponse).status()).toBe(202)
  await expect(page.getByText(/^completed · 100%/)).toBeVisible({ timeout: 30000 })
  await expect(page.getByTestId('workflow-stages').locator('div')).toHaveCount(7)
  await expect(page.getByTestId('workflow-stages').getByText('completed', { exact: true })).toHaveCount(7)
  await shot('09-c-stages.png')

  // 7. Compatible triple compare with model-derived values.
  await page.getByRole('tab', { name: 'Compare' }).click()
  await expect(page.getByText('Compatible pinned runs')).toBeVisible()
  const grid = page.getByTestId('compare-grid')
  await expect(grid.getByText(/Supplier B/).first()).toBeVisible()
  await expect(grid.getByText(/Supplier C/).first()).toBeVisible()
  await expect(grid.getByText(/-18\.75/)).toBeVisible()
  await expect(grid.getByText(/\+6\.25/)).toBeVisible()
  await shot('10-compare.png')

  // 8. Evidence and atomic context switching.
  await page.getByRole('tab', { name: 'Evidence' }).click()
  await expect(page.getByText(/Input digest:/)).toBeVisible()
  await page.getByTestId('case-supplier_b').click()
  await expect(page.getByText('Scenario Summary · Supplier B')).toBeVisible()
  await page.getByTestId('case-supplier_c').click()
  await page.getByRole('tab', { name: 'Evidence' }).click()
  await shot('11-evidence.png')

  // 9. Refresh and URL back/forward recovery; service restart is checked separately.
  await page.reload()
  await expect(page.getByTestId('case-supplier_c')).toBeVisible()
  await page.getByRole('tab', { name: 'Compare' }).click()
  await expect(page.getByText('Compatible pinned runs')).toBeVisible()
  await page.getByTestId('case-supplier_b').click()
  await page.getByTestId('case-supplier_c').click()
  await page.goBack()
  await expect(page).toHaveURL(/case=supplier_b/)
  await expect(page.getByText('Scenario Summary · Supplier B')).toBeVisible()
  await page.goForward()
  await expect(page).toHaveURL(/case=supplier_c/)
  await expect(page.getByText('Scenario Summary · Supplier C')).toBeVisible()
  await shot('12-recovery.png')
  expect(errors).toEqual([])
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth > window.innerWidth + 1)
  expect(overflow).toBe(false)
})
