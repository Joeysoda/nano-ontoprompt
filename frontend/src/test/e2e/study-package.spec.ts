import { expect, test } from '@playwright/test'

test('export, inspect and install a published Study as a rebound draft', async ({ page }) => {
  const ontologyId = 'package-target'
  const studyId = 'portable-study'
  const spec = {
    spec_version: 'scenario-study/v1', context_policy: {}, parameters: [],
    nodes: [{ key: 'read', kind: 'object_query', inputs: {}, outputs: { objects: 'object_set' }, config: { type: 'Operation', select: ['runtime_minutes'] } }],
    edges: [], metrics: [], constraints: [], budgets: {},
  }
  const exported = { format: 'ontology-study-package/v1', name: 'Portable capacity', description: '', source: { study_id: studyId, version: 1, spec_hash: 'sha256-source' }, spec, dependencies: [], package_digest: 'sha256-package' }
  let installedBody: Record<string, unknown> | null = null

  await page.addInitScript(() => {
    localStorage.setItem('token', 'package-test')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'package-test', user: { id: 'test-user', username: 'admin', role: 'admin' } }, version: 0 }))
  })
  await page.route(`**/api/v2/ontologies/${ontologyId}/**`, async route => {
    const request = route.request()
    const path = new URL(request.url()).pathname.split(`/ontologies/${ontologyId}`)[1]
    const json = (body: unknown, status = 200) => route.fulfill({ status, contentType: 'application/json', body: JSON.stringify(body) })
    const study = { id: studyId, ontology_id: ontologyId, name: 'Portable capacity', description: '', status: 'published', draft_spec: spec, latest_version: 1, etag: 1 }
    if (path === '/scenario-study-definitions') return json({ items: [study] })
    if (path === `/scenario-study-definitions/${studyId}`) return json({ ...study, versions: [{ version: 1, compatibility: 'compatible', spec_hash: 'sha256-source', change_note: '', spec }] })
    if (path === `/scenario-study-definitions/${studyId}/versions/1:export`) return json(exported)
    if (path === '/scenario-study-packages:inspect') return json({ valid: true, issues: [], rebound_spec: { ...spec, nodes: [{ ...spec.nodes[0], config: { type: 'Job', select: ['elapsed_seconds'] } }] }, package_digest: 'sha256-package' })
    if (path === '/scenario-study-packages:install') { installedBody = request.postDataJSON(); return json({ ...study, id: 'installed-study', name: 'Installed capacity', status: 'draft', latest_version: 0 }, 201) }
    if (path === '/scenario-study-templates') return json({ items: [] })
    if (path === '/logic-assets') return json({ assets: [] })
    if (path === '/scenarios') return json({ scenarios: [] })
    if (path.endsWith('/cases')) return json({ items: [] })
    if (path.endsWith('/compare')) return json({ cases: [] })
    return json({ detail: `unmocked ${request.method()} ${path}` }, 500)
  })

  await page.goto(`/ontologies/${ontologyId}/scenario-studies?study=${studyId}`)
  await page.getByText('跨本体复用 · 导出、重绑定与安装').click()
  await page.getByRole('button', { name: '导出 v1' }).click()
  await expect(page.getByLabel('Study 应用包 JSON')).toContainText('ontology-study-package/v1')
  await page.getByLabel('Study 应用包绑定 JSON').fill(JSON.stringify({ types: { Operation: 'Job' }, properties: { Operation: { runtime_minutes: 'elapsed_seconds' } } }))
  await page.getByRole('button', { name: '预检目标本体' }).click()
  await expect(page.getByRole('status')).toContainText('目标本体兼容')
  await expect(page.getByRole('button', { name: '安装为草稿' })).toBeEnabled()
  await page.getByLabel('安装后的研究名称').fill('Installed capacity')
  await page.getByRole('button', { name: '安装为草稿' }).click()
  await expect(page.getByRole('status')).toContainText('已安装为草稿')
  expect(installedBody).toMatchObject({ name: 'Installed capacity', bindings: { types: { Operation: 'Job' } } })
})
