import { expect, test } from '@playwright/test'

test('configure, run and schedule a derived projection from published resources', async ({ page }) => {
  const ontologyId = 'projection-test'
  let projection: Record<string, unknown> | null = null
  const posts: string[] = []
  await page.addInitScript(() => {
    localStorage.setItem('token', 'projection-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'projection-test', user: { id: 'u1', username: 'admin', role: 'admin' } }, version: 0 }))
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (request.method() === 'POST') posts.push(path)
    if (path === `/api/v1/ontologies/${ontologyId}`) return json({ data: { id: ontologyId, name: 'Factory', status: 'published', domain: 'manufacturing' } })
    if (path.endsWith('/logic-assets')) return json({ assets: [{ id: 'a1', asset_key: 'observation_summary_v1', version: '1.0.0', name: '观测统计摘要', kind: 'statistics', description: '', implementation: 'observation_summary_v1', status: 'published', interface_key: 'stats', interface_version: '1.0.0', executor_type: 'local', input_schema: { required: ['values'] } }] })
    if (path.endsWith('/queries')) return json({ queries: [{ api_name: 'machine_readings', latest_version: '1.0.0', output_schema: { columns: [{ name: 'sensor_value' }] } }] })
    if (path.endsWith('/derived-projections') && request.method() === 'GET') return json({ projections: projection ? [projection] : [] })
    if (path.endsWith('/derived-projections') && request.method() === 'POST') {
      projection = { id: 'p1', api_name: 'equipment_observation_summary', name: '设备观测持续摘要', status: 'paused', interval_minutes: 15, etag: 1, freshness: { status: 'never_run' } }
      return json(projection, 201)
    }
    if (path.endsWith('/derived-projections/p1:run')) {
      projection = { ...projection, freshness: { status: 'fresh' } }; return json({ id: 'r1', status: 'completed', output: { count: 2 } })
    }
    if (path.endsWith('/derived-projections/p1') && request.method() === 'PATCH') {
      projection = { ...projection, status: 'active', etag: 2 }; return json(projection)
    }
    if (path.endsWith('/alerts')) return json({ alerts: [], total: 0 })
    if (path.endsWith('/rules')) return json({ rules: [] })
    return json({ data: [], items: [] })
  })

  await page.goto(`/ontologies/${ontologyId}?tab=logic-assets`)
  const panel = page.getByRole('region', { name: '持续派生计算' })
  await expect(panel).toBeVisible()
  await expect(panel.getByText('sensor_value')).toBeVisible()
  await panel.getByLabel('派生字段映射 JSON').fill('{"fields":[{"target":"value","source":["sensor_value"],"data_type":"number","required":true,"nullable":false}]}')
  await panel.getByRole('button', { name: '保存派生投影' }).click()
  await expect(panel.getByText(/never_run/)).toBeVisible()
  await panel.getByRole('button', { name: '立即运行' }).click()
  await expect(panel.getByText(/freshness: fresh/)).toBeVisible()
  await panel.getByRole('button', { name: '启用调度' }).click()
  await expect(panel.getByText(/active/)).toBeVisible()
  expect(posts.some(path => path.endsWith('/derived-projections/p1:run'))).toBeTruthy()
})
