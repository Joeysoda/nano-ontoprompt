import { expect, test } from '@playwright/test'

test('create and publish a reusable process model into guarded Actions', async ({ page }) => {
  const ontologyId = 'process-test'
  let model: Record<string, unknown> | null = null
  await page.addInitScript(() => {
    localStorage.setItem('token', 'process-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'process-test', user: { id: 'u1', username: 'editor', role: 'editor' } }, version: 0 }))
  })
  await page.route(`**/api/v2/ontologies/${ontologyId}/**`, route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    if (path.endsWith('/scenarios')) return json({ scenarios: [] })
    if (path.endsWith('/scenario-action-types')) return json({ action_types: [] })
    if (path.endsWith('/process-models') && request.method() === 'GET') return json({ process_models: model ? [model] : [] })
    if (path.endsWith('/process-models') && request.method() === 'POST') {
      const body = request.postDataJSON(); model = { id: 'm1', ...body, states: body.definition.states, transitions: body.definition.transitions, status: 'draft', version: 0, etag: 1 }; return json(model, 201)
    }
    if (path.endsWith('/process-models/m1:publish')) { model = { ...model, status: 'published', version: 1, etag: 2 }; return json({ ...model, actions: [{ id: 'a1', transition_key: 'assign', status: 'published' }] }) }
    return json({})
  })
  await page.goto(`/ontologies/${ontologyId}/scenarios`)
  const panel = page.getByRole('region', { name: '流程模型配置' })
  await expect(panel).toBeVisible()
  await panel.getByRole('button', { name: '保存流程草稿' }).click()
  await expect(panel.getByText(/draft v0/)).toBeVisible()
  await panel.getByRole('button', { name: '发布并生成 Action' }).click()
  await expect(panel.getByText(/published v1/)).toBeVisible()
  await expect(panel.getByRole('status')).toContainText('带前置状态和权限的 Action')
})
