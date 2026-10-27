import { expect, test } from '@playwright/test'

test('run reusable propagation and optimization assets from user-authored configuration', async ({ page }) => {
  const ontologyId = 'generic-reasoning'
  const assets = [
    { id: 'prop', asset_key: 'configurable_propagation_v1', version: '1.0.0', name: '可配置依赖传播', kind: 'reasoning', description: 'generic', implementation: 'configurable_propagation_v1', status: 'published', interface_key: 'manufacturing.configurable_propagation', interface_version: '1.0.0', executor_type: 'local', input_schema: { required: ['nodes', 'edges', 'seeds'] } },
    { id: 'opt', asset_key: 'linear_optimization_v1', version: '1.0.0', name: '通用线性与整数优化', kind: 'optimization', description: 'generic', implementation: 'linear_optimization_v1', status: 'published', interface_key: 'manufacturing.linear_optimization', interface_version: '1.0.0', executor_type: 'local', input_schema: { required: ['variables', 'constraints'] } },
    { id: 'process', asset_key: 'process_analysis_v1', version: '1.0.0', name: '通用流程事件分析', kind: 'process_analysis', description: 'generic', implementation: 'process_analysis_v1', status: 'published', interface_key: 'manufacturing.process_analysis', interface_version: '1.0.0', executor_type: 'local', input_schema: { required: ['events'] } },
  ]
  const submitted: Array<Record<string, unknown>> = []
  await page.addInitScript(() => {
    localStorage.setItem('token', 'generic-reasoning')
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'generic-reasoning', user: { id: 'u1', username: 'admin', role: 'admin' } }, version: 0 }))
  })
  await page.route(url => url.pathname.startsWith('/api/'), async route => {
    const request = route.request(), path = new URL(request.url()).pathname
    const json = (body: unknown) => route.fulfill({ status: 200, contentType: 'application/json', body: JSON.stringify(body) })
    if (path === `/api/v1/ontologies/${ontologyId}`) return json({ data: { id: ontologyId, name: 'Factory', status: 'published', domain: 'manufacturing' } })
    if (path.endsWith('/logic-assets') && request.method() === 'GET') return json({ assets })
    if (path.endsWith('/logic-assets/prop/run')) {
      submitted.push(request.postDataJSON().inputs)
      return json({ output: { impacts: [{ node: 'order', value: 12 }], paths: [], complete: true }, version: '1.0.0', trace: [] })
    }
    if (path.endsWith('/logic-assets/opt/run')) {
      submitted.push(request.postDataJSON().inputs)
      return json({ output: { status: 'optimal', objective: 20, values: [{ variable: 'product_a', value: 4 }], solver: 'highs-via-scipy-milp' }, version: '1.0.0', trace: [] })
    }
    if (path.endsWith('/logic-assets/process/run')) {
      submitted.push(request.postDataJSON().inputs)
      return json({ output: { case_count: 1, rework_case_count: 0, variants: [] }, version: '1.0.0', trace: [] })
    }
    if (path.endsWith('/queries')) return json({ queries: [] })
    if (path.endsWith('/derived-projections')) return json({ projections: [] })
    if (path.endsWith('/alerts')) return json({ alerts: [], total: 0 })
    if (path.endsWith('/rules')) return json({ rules: [] })
    return json({ data: [], items: [] })
  })

  await page.goto(`/ontologies/${ontologyId}?tab=logic-assets`)
  await page.getByRole('button', { name: /可配置依赖传播/ }).click()
  const assetInput = page.locator('textarea').first()
  await expect(assetInput).toHaveValue(/supplier/)
  await page.getByRole('button', { name: '执行本地逻辑' }).click()
  await expect(page.getByText(/"value": 12/)).toBeVisible()

  await page.getByRole('button', { name: /通用线性与整数优化/ }).click()
  await expect(assetInput).toHaveValue(/objective_sense/)
  await page.getByRole('button', { name: '执行本地逻辑' }).click()
  await expect(page.getByText(/"objective": 20/)).toBeVisible()
  await page.getByRole('button', { name: /通用流程事件分析/ }).click()
  await expect(assetInput).toHaveValue(/work-order-1/)
  await page.getByRole('button', { name: '执行本地逻辑' }).click()
  await expect(page.getByText(/"case_count": 1/)).toBeVisible()
  expect(submitted).toHaveLength(3)
  expect(submitted[0]).toHaveProperty('edges')
  expect(submitted[1]).toHaveProperty('constraints')
  expect(submitted[2]).toHaveProperty('events')
})
