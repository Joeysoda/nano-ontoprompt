import { expect, test } from '@playwright/test'

// Browser component/authoring contract. API persistence is simulated;
// backend mapping execution is covered independently by test_row_mapping.py.
test('author a field mapping without editing JSON and retain it after refresh', async ({ page }) => {
  const empty = { spec_version: 'scenario-study/v1', parameters: [], nodes: [], edges: [], metrics: [], constraints: [], budgets: {} }
  let study = { id: 'mapping-study', ontology_id: 'mapping-test', name: '映射测试', description: '', status: 'draft', latest_version: 0, etag: 1, draft_spec: empty as Record<string, unknown> }
  const errors: string[] = []
  page.on('pageerror', error => errors.push(error.message))
  await page.addInitScript(() => {
    localStorage.setItem('token', 'mapping-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'mapping-test', user: { id: 'test-user', username: 'admin', role: 'admin' } }, version: 0 }))
  })
  await page.route('**/api/v1/ontologies?*', route => route.fulfill({ json: { items: [], total: 0 } }))
  await page.route('**/api/v2/ontologies/mapping-test/**', async route => {
    const path = new URL(route.request().url()).pathname
    if (route.request().method() === 'PATCH') {
      const body = route.request().postDataJSON()
      study = { ...study, draft_spec: body.draft_spec, etag: study.etag + 1 }
      return route.fulfill({ json: study })
    }
    if (path.endsWith('/scenario-study-definitions')) return route.fulfill({ json: { items: [study] } })
    if (path.endsWith('/scenario-study-definitions/mapping-study')) return route.fulfill({ json: { ...study, versions: [] } })
    return route.fulfill({ json: { items: [], assets: [], scenarios: [] } })
  })
  await page.goto('/ontologies/mapping-test/scenario-studies?study=mapping-study')
  await page.getByLabel('添加节点类型').selectOption('row_mapping')
  await page.getByRole('button', { name: '＋ 添加计算节点' }).click()
  const editor = page.getByRole('region', { name: '字段映射配置' })
  await editor.getByRole('button', { name: '＋ 添加字段映射' }).click()
  await editor.getByLabel('目标字段', { exact: true }).fill('duration')
  await editor.getByLabel('来源路径', { exact: true }).fill('properties.runtime_minutes')
  await editor.getByLabel('来源单位', { exact: true }).fill('min')
  await editor.getByLabel('目标单位', { exact: true }).fill('s')
  await page.getByRole('button', { name: '保存草稿', exact: true }).click()
  await expect.poll(() => study.etag).toBe(2)
  await page.reload()
  await expect(page.getByLabel('目标字段', { exact: true })).toHaveValue('duration')
  await expect(page.getByLabel('来源路径', { exact: true })).toHaveValue('properties.runtime_minutes')
  await expect(page.getByLabel('来源单位', { exact: true })).toHaveValue('min')
  await page.getByRole('button', { name: '删除映射', exact: true }).click()
  await expect(page.getByLabel('目标字段', { exact: true })).toHaveCount(0)
  expect(errors).toEqual([])
})
