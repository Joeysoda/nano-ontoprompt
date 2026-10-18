import { test, expect, type Page } from '@playwright/test';

const catalog = { metadata_digest: 'metadata-1', types: [
  { kind: 'object', api_name: 'Machine', properties: [{ api_name: 'line', data_type: 'string', searchable: true, aggregatable: true }] },
], links: [] };

const openAlert = {
  id: 'alert-open', rule_id: 'rule-1', status: 'open', severity: 'high',
  window_start: '2018-01-02T01:45:00+00:00', window_end: '2018-01-02T03:00:00+00:00',
  evidence: { peak: 121.79, breaching_windows: 3, ongoing: false }, properties: { assignee: 'energy-desk' },
  suggested_action: { action_id: 'inspect', request: { target_object_id: 'm1', parameters: { reason: 'rule' } } },
  occurrence_count: 2, recovered_at: '2018-01-02T03:15:00+00:00', resolution_note: '',
};
const resolvedAlert = { ...openAlert, id: 'alert-done', status: 'resolved', severity: 'low', suggested_action: null, occurrence_count: 1, resolution_note: '已更换轴承' };

async function mount(page: Page) {
  await page.route('**/object-query/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/object-sets/resources', route => route.fulfill({ json: { resources: [] } }));
  await page.route('**/object-query/aggregate', route => route.fulfill({ json: { groups: [], exact: true, completeness: 'complete' } }));
  await page.route('**/object-views/*', route => route.fulfill({ json: { configured: null, configured_version: null } }));
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [{ object_type: 'Machine', object_id: 'm1', properties: { line: 'L1' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await page.goto('/login?view=results');
  await page.addScriptTag({ type: 'module', content: `import {mount} from '/src/test/whatifHarness.tsx'; import Component from '/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx'; mount(Component);` });
  await expect(page.locator('#repair-test')).toBeAttached();
}

test('Object Panel lists rule alerts, acknowledges one, and previews the suggested Action without submitting', async ({ page }) => {
  let alerts = [openAlert, resolvedAlert];
  const posts: string[] = [];
  const queries: string[] = [];
  await page.route('**/ontologies/repair/alerts?*', route => { queries.push(new URL(route.request().url()).search); return route.fulfill({ json: { alerts, total: alerts.length } }); });
  await page.route('**/ontologies/repair/alerts/*', route => {
    posts.push(new URL(route.request().url()).pathname.split('/').at(-1) || '');
    alerts = alerts.map(alert => alert.id === 'alert-open' ? { ...alert, status: 'acknowledged' } : alert);
    return route.fulfill({ json: alerts[0] });
  });
  await page.route('**/ontologies/repair/actions/inspect/*', route => {
    posts.push(new URL(route.request().url()).pathname.split('/').at(-1) || '');
    expect(route.request().postDataJSON()).toMatchObject({ target_object_id: 'm1', parameters: { reason: 'rule' }, context: { consistency: 'live' } });
    return route.fulfill({ json: { target: 'live', edit_count: 1, edits: [] } });
  });
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'm1' }).click();
  const section = ui.getByRole('region', { name: '规则告警' });
  await expect(section.getByText('规则告警 · 1 条未解决')).toBeVisible();
  await expect(section.getByTestId('rule-alert')).toHaveCount(1);
  await expect(section.getByText('条件已恢复')).toBeVisible();
  await expect(section.getByText('峰值 121.79，越线窗口 3')).toBeVisible();
  expect(queries[0]).toContain('subject_type=Machine');
  expect(queries[0]).toContain('subject_id=m1');

  await section.getByRole('button', { name: '预览建议操作' }).click();
  await expect(section.getByText('预览：将对 live 产生 1 项编辑；请通过“操作”确认提交')).toBeVisible();
  await section.getByRole('button', { name: '确认' }).click();
  await expect(section.getByText('已确认')).toBeVisible();
  await expect(section.getByRole('button', { name: '确认' })).toHaveCount(0);
  expect(posts).toEqual(['preview', 'alert-open:acknowledge']);

  await section.getByRole('button', { name: '显示已解决（1）' }).click();
  await expect(section.getByText('说明：已更换轴承')).toBeVisible();
});

test('alert permission errors are explicit and retry recovers', async ({ page }) => {
  let denied = true;
  await page.route('**/ontologies/repair/alerts?*', route => denied
    ? route.fulfill({ status: 403, json: { detail: { code: 'forbidden' } } })
    : route.fulfill({ json: { alerts: [], total: 0 } }));
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'm1' }).click();
  const section = ui.getByRole('region', { name: '规则告警' });
  await expect(section.getByRole('alert')).toContainText('没有操作权限');
  denied = false;
  await section.getByRole('button', { name: '重试' }).click();
  await expect(section.getByText('暂无未解决的告警。')).toBeVisible();
});

test('pinned Object Panel lists linked Event Objects newest first via reverse traversal', async ({ page }) => {
  const eventCatalog = { metadata_digest: 'metadata-1', types: [
    { kind: 'object', api_name: 'Machine', properties: [{ api_name: 'model', data_type: 'string', searchable: true, aggregatable: true }] },
    { kind: 'object', api_name: 'FailureEvent', properties: [{ api_name: 'start_timestamp', data_type: 'datetime' }, { api_name: 'end_timestamp', data_type: 'datetime' }, { api_name: 'code', data_type: 'string' }] },
    { kind: 'object', api_name: 'MaintenanceEvent', properties: [{ api_name: 'start_timestamp', data_type: 'datetime' }, { api_name: 'end_timestamp', data_type: 'datetime' }, { api_name: 'code', data_type: 'string' }] },
  ], links: [{ api_name: 'FAILURE_OF', source_type: 'FailureEvent', target_type: 'Machine' }, { api_name: 'MAINTENANCE_OF', source_type: 'MaintenanceEvent', target_type: 'Machine' }] };
  const traversals: Array<{ link: { api_name: string; direction: string }; context: { data_view_id: string } }> = [];
  await page.route('**/object-query/catalog', route => route.fulfill({ json: eventCatalog }));
  await page.route('**/object-sets/resources', route => route.fulfill({ json: { resources: [] } }));
  await page.route('**/object-query/aggregate', route => route.fulfill({ json: { groups: [], exact: true, completeness: 'complete' } }));
  await page.route('**/object-views/*', route => route.fulfill({ json: { configured: null, configured_version: null } }));
  await page.route('**/ontologies/repair/alerts?*', route => route.fulfill({ json: { alerts: [], total: 0 } }));
  await page.route('**/object-query/load', route => {
    const body = route.request().postDataJSON();
    if (body.expression.kind === 'traverse') {
      traversals.push({ link: body.expression.link, context: body.context });
      const failure = body.expression.link.api_name === 'FAILURE_OF';
      return route.fulfill({ json: { objects: failure
        ? [{ object_type: 'FailureEvent', object_id: 'f1', properties: { start_timestamp: '2015-03-06T06:00:00+00:00', code: 'comp1' } }]
        : [{ object_type: 'MaintenanceEvent', object_id: 'm1', properties: { start_timestamp: '2015-06-01T06:00:00+00:00', code: 'comp2' } }],
        completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } });
    }
    return route.fulfill({ json: { objects: [{ object_type: 'Machine', object_id: 'machine-001', properties: { model: 'model3' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } });
  });
  await page.goto('/login?view=results&dataView=view-1');
  await page.addScriptTag({ type: 'module', content: `import {mount} from '/src/test/whatifHarness.tsx'; import Component from '/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx'; mount(Component);` });
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'machine-001' }).click();
  const section = ui.getByRole('region', { name: '关联事件' });
  await expect(section.getByTestId('linked-event')).toHaveCount(2);
  await expect(section.getByTestId('linked-event').first()).toContainText('MaintenanceEvent');
  await expect(section.getByTestId('linked-event').last()).toContainText('comp1');
  expect(traversals.map(t => [t.link.api_name, t.link.direction, t.context.data_view_id]).sort()).toEqual([['FAILURE_OF', 'in', 'view-1'], ['MAINTENANCE_OF', 'in', 'view-1']]);
});
