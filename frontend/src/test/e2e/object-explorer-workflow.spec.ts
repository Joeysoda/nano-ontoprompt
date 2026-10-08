import { test, expect, type Page } from '@playwright/test';

const catalog = { metadata_digest: 'metadata-1', types: [
  { kind: 'object', api_name: 'Order', properties: [{ api_name: 'status', data_type: 'string', searchable: true, aggregatable: true }] },
  { kind: 'object', api_name: 'Supplier', properties: [] },
], links: [{ api_name: 'SUPPLIED_BY', source_type: 'Order', target_type: 'Supplier' }] };

async function mount(page: Page) {
  await page.route('**/object-query/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/object-sets/resources', route => route.fulfill({ json: { resources: [] } }));
  await page.route('**/object-query/aggregate', route => route.fulfill({ json: { groups: [{ count: 2 }], exact: true, completeness: 'complete' } }));
  await page.route('**/object-query/data-views', route => route.fulfill({ json: { view_id: 'view-1', status: 'ready' } }));
  await page.goto('/login?view=results');
  await page.addScriptTag({ type: 'module', content: `import {mount} from '/src/test/whatifHarness.tsx'; import Component from '/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx'; mount(Component);` });
  await expect(page.locator('#repair-test')).toBeAttached();
}

test('List saves all pinned pages; Exploration saves the definition, not visible members', async ({ page }) => {
  const saved: Array<{ description?: string; definition: { expression: { kind: string; object_ids?: string[] } } }> = [];
  await page.route('**/object-query/load', route => {
    const body = route.request().postDataJSON();
    const second = body.read.page_token === 'next';
    return route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: second ? 'second' : 'first', properties: { status: 'open' } }], completeness: second ? 'complete' : 'partial', status: 'ok', page: { returned: 1, has_more: !second, next_page_token: second ? null : 'next' } } });
  });
  await mount(page);
  await page.route('**/object-sets/resources', route => {
    if (route.request().method() === 'POST') { saved.push(route.request().postDataJSON()); return route.fulfill({ json: { id: 'saved', name: 'Orders' } }); }
    return route.fulfill({ json: { resources: [] } });
  });
  const ui = page.locator('#repair-test');
  await expect(ui.getByRole('button', { name: '保存 List（完整集合）' })).toBeDisabled();
  await ui.getByRole('button', { name: '保存 Exploration' }).click();
  const explorationDialog = page.getByRole('dialog', { name: '保存 Exploration' });
  await explorationDialog.getByLabel('集合名称', { exact: true }).fill('Orders');
  await explorationDialog.getByLabel('集合说明', { exact: true }).fill('Pending orders maintained by operations');
  await explorationDialog.getByRole('button', { name: '确认保存 Exploration' }).click();
  await expect.poll(() => saved.length).toBe(1);
  expect(saved[0].definition.expression).toEqual({ kind: 'base', type_ref: { kind: 'object', api_name: 'Order' } });
  expect(saved[0].description).toBe('Pending orders maintained by operations');
  await ui.getByRole('button', { name: '固定当前数据' }).click();
  await expect(page).toHaveURL(/dataView=view-1/);
  await ui.getByRole('button', { name: '保存 List（完整集合）' }).click();
  const listDialog = page.getByRole('dialog', { name: '保存 List' });
  await listDialog.getByLabel('集合名称', { exact: true }).fill('Orders');
  await listDialog.getByRole('button', { name: '确认保存 List' }).click();
  await expect.poll(() => saved.length).toBe(2);
  expect(saved[1].definition.expression.object_ids).toEqual(['first', 'second']);
});

test('Search Around preserves expression and browser history restores the prior type', async ({ page }) => {
  const expressions: Array<{ kind: string }> = [];
  await page.route('**/object-query/load', route => {
    expressions.push(route.request().postDataJSON().expression);
    return route.fulfill({ json: { objects: [], completeness: 'complete', status: 'ok', page: { returned: 0, has_more: false } } });
  });
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByLabel('Search Around', { exact: true }).selectOption('0');
  await expect(ui.getByLabel('对象类型', { exact: true })).toHaveValue('Supplier');
  await expect.poll(() => expressions.some(item => item.kind === 'traverse')).toBeTruthy();
  await page.goBack();
  await expect(ui.getByLabel('对象类型', { exact: true })).toHaveValue('Order');
  await page.goForward();
  await expect(ui.getByLabel('对象类型', { exact: true })).toHaveValue('Supplier');
});

test('expired views are explicit and incomplete pages cannot be saved as complete Lists', async ({ page }) => {
  let posts = 0;
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [], completeness: 'incomplete', status: 'partial', page: { returned: 0, has_more: false } } }));
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: '固定当前数据' }).click();
  await expect(page).toHaveURL(/dataView=view-1/);
  await page.route('**/object-sets/resources', route => { if (route.request().method() === 'POST') posts++; return route.fulfill({ json: { resources: [] } }); });
  await ui.getByRole('button', { name: '保存 List（完整集合）' }).click();
  const listDialog = page.getByRole('dialog', { name: '保存 List' });
  await listDialog.getByLabel('集合名称', { exact: true }).fill('Incomplete');
  await listDialog.getByRole('button', { name: '确认保存 List' }).click();
  await expect(ui.getByRole('alert')).toContainText('Incomplete result');
  expect(posts).toBe(0);
  await listDialog.getByRole('button', { name: '关闭保存弹窗' }).click();
  await page.route('**/object-query/load', route => route.fulfill({ status: 410, json: { detail: { code: 'expired_reference' } } }));
  await ui.getByRole('button', { name: '刷新对象集合' }).click();
  await expect(ui.getByText('视图或游标已过期，请重新固定视图', { exact: false })).toBeVisible();
});

test('Object Panel previews an Action before committing to live', async ({ page }) => {
  const calls: Array<{ path: string; body: { target_object_id: string; parameters: Record<string, unknown>; context: { consistency: string } } }> = [];
  await page.route('**/ontologies/repair/action-runs*', route => route.fulfill({ json: [] }));
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-1', properties: { status: 'pending' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await page.route('**/ontologies/repair/actions', route => route.fulfill({ json: [{ id: 'approve', name: 'Approve order', target_entity_type: 'Order', enabled: true, status: 'published', parameters: [{ name: 'status', type: 'string', required: true }] }] }));
  await page.route('**/ontologies/repair/actions/approve/*', route => {
    const body = route.request().postDataJSON();
    calls.push({ path: new URL(route.request().url()).pathname, body });
    return route.fulfill({ json: route.request().url().endsWith('/preview') ? { target: 'live', edits: [{ op: 'invoke_action' }, { op: 'set_property', property: 'status', value: 'approved' }] } : { status: 'completed', target: 'live' } });
  });
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'order-1' }).click();
  await ui.getByRole('button', { name: 'Actions' }).click();
  const dialog = page.getByRole('dialog', { name: '执行对象 Action' });
  await dialog.getByRole('combobox', { name: 'Action' }).selectOption('approve');
  await dialog.getByRole('textbox', { name: 'status' }).fill('approved');
  await dialog.getByRole('button', { name: '预览' }).click();
  await expect(dialog.getByText('将提交 1 项编辑到 live')).toBeVisible();
  await dialog.getByRole('button', { name: '确认提交' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: 'Action 已提交到 live。' })).toBeVisible();
  expect(calls.map(item => item.path.split('/').at(-1))).toEqual(['preview', 'run']);
  expect(calls[0].body).toMatchObject({ target_object_id: 'order-1', parameters: { status: 'approved' }, context: { consistency: 'live' } });
});

test('workspace controls edit filters, columns, chart layout, and multi-object preview', async ({ page }) => {
  await page.route('**/object-query/load', route => route.fulfill({ json: {
    objects: [
      { object_type: 'Order', object_id: 'order-2', properties: { status: 'closed' } },
      { object_type: 'Order', object_id: 'order-1', properties: { status: 'open' } },
    ],
    completeness: 'complete', status: 'ok', page: { returned: 2, has_more: false },
  } }));
  await mount(page);
  const ui = page.locator('#repair-test');

  await ui.locator('.oe-query-summary').click();
  await expect(ui.getByRole('region', { name: '筛选器' })).toBeVisible();
  await ui.locator('.oe-filter-catalog button').first().click();
  await expect(ui.locator('.oe-query-builder .rule')).toHaveCount(1);
  await ui.getByRole('button', { name: 'Apply filters' }).click();
  await expect(ui.locator('.oe-filter-chip')).toContainText('status');

  await ui.getByRole('button', { name: /Columns/ }).click();
  const columnPanel = ui.getByRole('region', { name: '配置结果列' });
  await columnPanel.getByRole('checkbox', { name: 'status' }).uncheck();
  await expect(ui.getByRole('columnheader').filter({ hasText: 'status' })).toHaveCount(0);
  await columnPanel.getByRole('checkbox', { name: 'status' }).check();

  await ui.getByRole('checkbox', { name: '选择 order-2' }).check();
  await ui.getByRole('checkbox', { name: '选择 order-1' }).check();
  const selection = ui.getByRole('complementary', { name: 'Selection Preview' });
  await expect(selection).toContainText('order-2');
  await expect(selection).toContainText('order-1');

  await ui.getByRole('tab', { name: 'Explore' }).click();
  await expect(ui.getByRole('img', { name: 'status 当前页分布' })).toBeVisible();
  await ui.getByRole('button', { name: '删除 status 图表' }).click();
  await expect(ui.getByRole('img', { name: 'status 当前页分布' })).toHaveCount(0);
  await ui.getByRole('button', { name: '撤销图表布局' }).click();
  await expect(ui.getByRole('img', { name: 'status 当前页分布' })).toBeVisible();
});

test('published Object View tabs render and standard view remains available', async ({ page }) => {
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-1', properties: { status: 'pending', hidden: 'retained' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await page.route('**/object-views/Order', route => route.fulfill({ json: { configured_version: 2, configured: { tabs: [{ id: 'summary', title: 'Summary', properties: ['status'], layout: 'list', show_in_panel: true }] } } }));
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'order-1' }).click();
  await expect(ui.getByText('Summary').first()).toBeVisible();
  await ui.getByRole('button', { name: '完整对象视图' }).click();
  const dialog = page.getByRole('dialog', { name: /完整对象视图/ });
  await expect(dialog.getByText('Configured v2')).toBeVisible();
  await expect(dialog.getByText('retained')).toHaveCount(0);
  await dialog.getByRole('button', { name: 'Standard' }).click();
  await expect(dialog.getByText('retained')).toBeVisible();
});

test('Action form validates structured parameters before preview', async ({ page }) => {
  let previewBody: { parameters: Record<string, unknown> } | null = null;
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-1', properties: { status: 'pending' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await page.route('**/ontologies/repair/actions', route => route.fulfill({ json: [{ id: 'create', name: 'Create related', target_entity_type: 'Order', enabled: true, status: 'published', parameters: [{ name: 'data', type: 'object', required: true }, { name: 'active', type: 'boolean', required: true }] }] }));
  await page.route('**/ontologies/repair/action-runs*', route => route.fulfill({ json: [] }));
  await page.route('**/ontologies/repair/actions/create/preview', route => { previewBody = route.request().postDataJSON(); return route.fulfill({ json: { target: 'live', edits: [{ op: 'invoke_action' }] } }); });
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'order-1' }).click();
  await ui.getByRole('button', { name: 'Actions' }).click();
  const dialog = page.getByRole('dialog', { name: '执行对象 Action' });
  await dialog.getByRole('combobox', { name: 'Action' }).selectOption('create');
  await dialog.getByRole('textbox', { name: 'data' }).fill('{broken');
  await dialog.getByRole('button', { name: '预览' }).click();
  await expect(dialog.getByText('请先修正参数格式')).toBeVisible();
  expect(previewBody).toBeNull();
  await dialog.getByRole('textbox', { name: 'data' }).fill('{"name":"child"}');
  await dialog.getByRole('combobox', { name: 'active' }).selectOption('false');
  await dialog.getByRole('button', { name: '预览' }).click();
  await expect.poll(() => previewBody).toMatchObject({ parameters: { data: { name: 'child' }, active: false } });
});

test('slow loads expose a loading state and recover without a duplicate request', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let loads = 0;
  await page.route('**/object-query/load', async route => {
    loads += 1;
    await gate;
    await route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-slow', properties: { status: 'ready' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } });
  });
  await mount(page);
  const ui = page.locator('#repair-test');
  await expect(ui.getByRole('status', { name: '' }).filter({ hasText: '正在读取对象集合' })).toBeVisible();
  await expect(ui.getByRole('region', { name: 'Object Explorer' })).toHaveAttribute('aria-busy', 'true');
  expect(loads).toBe(1);
  release();
  await expect(ui.getByRole('button', { name: 'order-slow' })).toBeVisible();
  await expect(ui.getByRole('region', { name: 'Object Explorer' })).toHaveAttribute('aria-busy', 'false');
  expect(loads).toBe(1);
});

test('permission denial is distinct and retry recovers the same exploration', async ({ page }) => {
  let allowed = false;
  await page.route('**/object-query/load', route => allowed
    ? route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-recovered', properties: { status: 'open' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } })
    : route.fulfill({ status: 403, json: { detail: { code: 'permission_denied' } } }));
  await mount(page);
  const ui = page.locator('#repair-test');
  await expect(ui.getByRole('alert')).toContainText('没有操作权限');
  allowed = true;
  await ui.getByRole('button', { name: '重试' }).click();
  await expect(ui.getByRole('button', { name: 'order-recovered' })).toBeVisible();
});

test('Action conflict remains actionable and a busy submit cannot be duplicated', async ({ page }) => {
  let release!: () => void;
  const gate = new Promise<void>(resolve => { release = resolve; });
  let runCalls = 0;
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-1', properties: { status: 'pending' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await page.route('**/ontologies/repair/actions', route => route.fulfill({ json: [{ id: 'approve', name: 'Approve order', target_entity_type: 'Order', enabled: true, status: 'published', parameters: [] }] }));
  await page.route('**/ontologies/repair/action-runs*', route => route.fulfill({ json: [] }));
  await page.route('**/ontologies/repair/actions/approve/preview', route => route.fulfill({ json: { target: 'live', edits: [{ op: 'invoke_action' }, { op: 'set_property', property: 'status', value: 'approved' }] } }));
  await page.route('**/ontologies/repair/actions/approve/run', async route => {
    runCalls += 1;
    await gate;
    await route.fulfill({ status: 409, json: { detail: { code: 'edit_conflict', message: 'status 已被其他用户修改，请刷新' } } });
  });
  await mount(page);
  const ui = page.locator('#repair-test');
  await ui.getByRole('button', { name: 'order-1' }).click();
  await ui.getByRole('button', { name: 'Actions' }).click();
  const dialog = page.getByRole('dialog', { name: '执行对象 Action' });
  await dialog.getByRole('combobox', { name: 'Action' }).selectOption('approve');
  await dialog.getByRole('button', { name: '预览' }).click();
  const submit = dialog.getByRole('button', { name: '确认提交' });
  await submit.click();
  await expect(submit).toBeDisabled();
  await submit.click({ force: true });
  expect(runCalls).toBe(1);
  release();
  await expect(dialog.getByRole('alert')).toContainText('status 已被其他用户修改，请刷新');
  await expect(submit).toBeEnabled();
});

test('narrow layout contains horizontal overflow and tabs work from the keyboard', async ({ page }) => {
  await page.setViewportSize({ width: 390, height: 844 });
  await page.route('**/object-query/load', route => route.fulfill({ json: { objects: [{ object_type: 'Order', object_id: 'order-mobile', properties: { status: 'open' } }], completeness: 'complete', status: 'ok', page: { returned: 1, has_more: false } } }));
  await mount(page);
  const ui = page.locator('#repair-test');
  const results = ui.getByRole('tab', { name: 'Results' });
  await results.focus();
  await page.keyboard.press('Enter');
  await expect(results).toHaveAttribute('aria-selected', 'true');
  await expect(ui.getByRole('button', { name: 'order-mobile' })).toBeVisible();
  const overflow = await page.evaluate(() => document.documentElement.scrollWidth - document.documentElement.clientWidth);
  expect(overflow).toBeLessThanOrEqual(1);
});
