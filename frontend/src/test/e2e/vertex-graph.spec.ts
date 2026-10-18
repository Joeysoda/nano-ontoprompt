import { test, expect } from '@playwright/test';

const catalog = { metadata_digest: 'metadata-1', types: [
  { kind: 'object', api_name: 'Machine', properties: [{ api_name: 'model', data_type: 'string', searchable: true, aggregatable: true }] },
  { kind: 'object', api_name: 'FailureEvent', properties: [{ api_name: 'start_timestamp', data_type: 'datetime' }, { api_name: 'end_timestamp', data_type: 'datetime' }] },
], links: [{ api_name: 'FAILURE_OF', source_type: 'FailureEvent', target_type: 'Machine' }] };

const machine = { object_type: 'Machine', object_id: 'machine-001', properties: { model: 'model3' } };
const failures = [
  { object_type: 'FailureEvent', object_id: 'f-jan', properties: { start_timestamp: '2015-01-05T06:00:00+00:00' } },
  { object_type: 'FailureEvent', object_id: 'f-mar', properties: { start_timestamp: '2015-03-06T06:00:00+00:00' } },
];

test('Vertex graph: Search Around adds a typed set, layers and time cursor filter, versions save optimistically', async ({ page }) => {
  const graphCalls: Array<{ sets: Array<{ kind: string; link?: { api_name: string; direction: string } }> }> = [];
  const saves: Array<{ path: string; body: { expected_version?: number; definition: { sets: unknown[]; layers: Record<string, { color: string }> } } }> = [];
  await page.route('**/object-query/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/object-sets/resources', route => route.fulfill({ json: { resources: [] } }));
  await page.route('**/object-query/aggregate', route => route.fulfill({ json: { groups: [], exact: true, completeness: 'complete' } }));
  await page.route('**/object-views/*', route => route.fulfill({ json: { configured: null, configured_version: null } }));
  await page.route('**/ontologies/repair/alerts?*', route => route.fulfill({ json: { alerts: [], total: 0 } }));
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [machine], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await page.route('**/object-query/graph:multi', route => {
    const body = route.request().postDataJSON();
    graphCalls.push(body);
    const expanded = body.sets.length > 1;
    return route.fulfill({ json: { objects: expanded ? [...failures, machine] : [machine],
      edges: expanded ? failures.map(f => ({ source: { object_type: f.object_type, object_id: f.object_id }, target: { object_type: 'Machine', object_id: 'machine-001' }, relation_type: 'FAILURE_OF' })) : [] } });
  });
  let saveCount = 0;
  await page.route('**/ontologies/repair/graph-views**', route => {
    const path = new URL(route.request().url()).pathname;
    saves.push({ path, body: route.request().postDataJSON() });
    saveCount += 1;
    if (saveCount === 3) return route.fulfill({ status: 409, json: { detail: { code: 'version_conflict', head_version: 3 } } });
    return route.fulfill({ status: 201, json: { id: 'graph-1', head_version: saveCount, unchanged: false } });
  });
  await page.goto('/login?view=results&dataView=view-1');
  await page.addScriptTag({ type: 'module', content: `import {mount} from '/src/test/whatifHarness.tsx'; import Component from '/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx'; mount(Component);` });
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: '打开关系图' }).click();
  const graph = ui.getByRole('region', { name: '对象关系图' });
  const nodes = graph.getByRole('group', { name: '图对象列表' }).or(graph.locator('[aria-label="图对象列表"]'));
  await expect(nodes.getByRole('button')).toHaveCount(1);

  await nodes.getByRole('button', { name: 'Machine · machine-001' }).click();
  await graph.getByLabel('从选中对象 Search Around').selectOption({ label: 'FailureEvent → FAILURE_OF' });
  await expect(nodes.getByRole('button')).toHaveCount(3);
  expect(graphCalls.at(-1)?.sets.map(s => [s.kind, s.link?.api_name, s.link?.direction])).toEqual([['base', undefined, undefined], ['traverse', 'FAILURE_OF', 'in']]);
  await expect(graph.getByText('2 个对象集合')).toBeVisible();

  await graph.getByRole('checkbox', { name: 'FailureEvent' }).uncheck();
  await expect(nodes.getByRole('button')).toHaveCount(1);
  await graph.getByRole('checkbox', { name: 'FailureEvent' }).check();
  const slider = graph.getByLabel('时间游标');
  await slider.fill(String(Date.parse('2015-01-05T06:00:00Z')));
  await expect(nodes.getByRole('button', { name: 'FailureEvent · f-mar' })).toHaveCount(0);
  await expect(nodes.getByRole('button', { name: 'FailureEvent · f-jan' })).toHaveCount(1);

  await graph.getByLabel('图名称').fill('machine-001 failures');
  await graph.getByRole('button', { name: '保存图' }).click();
  await expect(graph.getByRole('status').filter({ hasText: '已保存为第 1 版' })).toBeVisible();
  await graph.getByRole('button', { name: '保存新版本（当前 v1）' }).click();
  await expect(graph.getByRole('status').filter({ hasText: '已保存为第 2 版' })).toBeVisible();
  await graph.getByRole('button', { name: '保存新版本（当前 v2）' }).click();
  await expect(graph.getByRole('status').filter({ hasText: '图已被他人更新' })).toBeVisible();
  expect(saves.map(s => s.path.split('/').slice(-2).join('/'))).toEqual(['repair/graph-views', 'graph-1/versions', 'graph-1/versions']);
  expect(saves[0].body.definition.sets).toHaveLength(2);
  expect(saves[1].body.expected_version).toBe(1);
  expect(Object.keys(saves[0].body.definition.layers).sort()).toEqual(['FailureEvent', 'Machine']);
});
