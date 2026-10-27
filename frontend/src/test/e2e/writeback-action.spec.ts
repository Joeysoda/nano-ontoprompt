import { expect, test } from '@playwright/test'

test('bind an Action external effect to an active REST connection', async ({ page }) => {
  const ontologyId = 'writeback-test'
  let saved: Record<string, unknown> | null = null
  await page.addInitScript(() => {
    localStorage.setItem('token', 'writeback-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'writeback-test', user: { id: 'u1', username: 'editor', role: 'editor' } }, version: 0 }))
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === '/api/v2/connections') return json([{ id: 'rest-1', name: 'Plant MES', kind: 'rest', status: 'active' }])
    if (path.endsWith('/scenarios')) return json({ scenarios: [{ id: 's1', name: 'Release plan', description: '', base_view_id: 'v1', head_revision: 0, etag: 1, status: 'active', protected_demo: false, ttl_seconds: 86400, mode: 'pinned' }] })
    if (path.endsWith('/scenarios/s1/workspace')) return json({ scenario: { id: 's1' }, revision: 0, view_id: 'v1', actions: [], base_objects: [], objects: [], changes: { created: [], deleted: [], modified: [] }, edits: [] })
    if (path.endsWith('/scenario-action-types') && request.method() === 'GET') return json({ action_types: [] })
    if (path.endsWith('/scenario-action-types') && request.method() === 'POST') {
      saved = request.postDataJSON()
      return json({ id: 'a1', ...saved, version: 1, status: 'draft', enabled: true }, 201)
    }
    if (path.endsWith('/process-models')) return json({ process_models: [] })
    return json({})
  })

  await page.goto(`/ontologies/${ontologyId}/scenarios?scenario=s1`)
  await page.getByRole('tab', { name: '操作' }).click()
  await page.getByRole('button', { name: '设计操作类型' }).click()
  await page.getByLabel('Name', { exact: true }).fill('Release work order')
  await page.getByText('外部系统写回').click()
  await page.getByLabel('Writeback connection').selectOption('rest-1')
  await page.getByLabel('Writeback endpoint').fill('/work-orders/release')
  await page.getByRole('button', { name: '保存操作类型' }).click()
  await expect.poll(() => saved).not.toBeNull()
  expect(saved).toMatchObject({ external_effect: { kind: 'rest_writeback', connection_id: 'rest-1', resource: '/work-orders/release' } })
})
