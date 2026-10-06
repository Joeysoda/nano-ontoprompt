import { expect, test, type Page } from '@playwright/test';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const evidenceDirectory = path.resolve(
  path.dirname(fileURLToPath(import.meta.url)),
  '../../../../backend/evidence',
);

const catalog = {
  metadata_digest: 'visual-evidence-metadata-1',
  types: [
    {
      kind: 'object',
      api_name: 'Order',
      properties: [
        { api_name: 'status', data_type: 'string', searchable: true, aggregatable: true },
        { api_name: 'priority', data_type: 'integer', searchable: true, aggregatable: true },
        { api_name: 'region', data_type: 'string', searchable: true, aggregatable: true },
        { api_name: 'created_at', data_type: 'date', searchable: true, aggregatable: true },
      ],
    },
    {
      kind: 'object',
      api_name: 'Supplier',
      properties: [
        { api_name: 'name', data_type: 'string', searchable: true, aggregatable: true },
        { api_name: 'risk', data_type: 'string', searchable: true, aggregatable: true },
      ],
    },
  ],
  links: [{ api_name: 'SUPPLIED_BY', source_type: 'Order', target_type: 'Supplier' }],
};

const orders = [
  { object_type: 'Order', object_id: 'ORD-1042', properties: { status: 'pending', priority: 5, region: 'North', created_at: '2026-10-01' } },
  { object_type: 'Order', object_id: 'ORD-1048', properties: { status: 'approved', priority: 2, region: 'West', created_at: '2026-10-02' } },
  { object_type: 'Order', object_id: 'ORD-1051', properties: { status: 'pending', priority: 4, region: 'North', created_at: '2026-10-03' } },
  { object_type: 'Order', object_id: 'ORD-1060', properties: { status: 'reviewing', priority: 3, region: 'South', created_at: '2026-10-04' } },
];

async function mount(page: Page, url = '/login?view=results') {
  await page.setViewportSize({ width: 1600, height: 1000 });
  await page.route('**/object-query/catalog', route => route.fulfill({ json: catalog }));
  await page.route('**/object-sets/resources', route => route.fulfill({ json: { resources: [] } }));
  await page.route('**/object-query/aggregate', route => route.fulfill({
    json: {
      groups: [
        { status: 'pending', count: 2 },
        { status: 'approved', count: 1 },
        { status: 'reviewing', count: 1 },
      ],
      exact: true,
      completeness: 'complete',
    },
  }));
  await page.route('**/object-query/data-views', route => route.fulfill({ json: { view_id: 'view-baseline', status: 'ready' } }));
  await page.goto(url);
  await page.addScriptTag({
    type: 'module',
    content: `import {mount} from '/src/test/whatifHarness.tsx'; import Component from '/src/pages/ontologies/detail/tabs/ObjectQueryTab.tsx'; mount(Component);`,
  });
  await page.addStyleTag({
    content: `
      body { margin: 0; background: #eef1f5; }
      #repair-test { display: block; min-width: 0; padding: 24px; background: #eef1f5; }
      * { animation-duration: 0s !important; transition-duration: 0s !important; }
    `,
  });
  await expect(page.locator('#repair-test').getByRole('region', { name: 'Object Explorer' })).toBeVisible();
}

async function routeOrders(page: Page) {
  await page.route('**/object-query/load', route => {
    const body = route.request().postDataJSON() as {
      expression: { kind: string };
      context: { data_view_id?: string };
    };
    const expression = body.expression;
    const objects = expression.kind === 'traverse'
      ? [
          { object_type: 'Supplier', object_id: 'SUP-201', properties: { name: 'Apex Components', risk: 'low' } },
          { object_type: 'Supplier', object_id: 'SUP-224', properties: { name: 'Northstar Metals', risk: 'medium' } },
        ]
      : orders;
    return route.fulfill({
      json: {
        objects,
        completeness: 'complete',
        status: 'ok',
        page: {
          page_size: 50,
          returned: objects.length,
          has_more: false,
          stability: body.context.data_view_id ? 'pinned' : 'live',
        },
      },
    });
  });
}

async function capture(page: Page, filename: string) {
  await page.screenshot({
    path: path.join(evidenceDirectory, filename),
    animations: 'disabled',
    fullPage: false,
  });
}

test('captures the default Results workspace and save controls', async ({ page }) => {
  await routeOrders(page);
  await mount(page);
  await expect(page.getByRole('button', { name: 'ORD-1042' })).toBeVisible();
  await capture(page, 'plan_b_current_results_save_2026-10-05.png');
  await page.getByRole('button', { name: /Columns/ }).click();
  await expect(page.getByRole('region', { name: '配置结果列' })).toBeVisible();
  await capture(page, 'plan_b_current_results_columns_2026-10-05.png');
  await page.getByRole('button', { name: '关闭列配置' }).click();
  await page.getByRole('button', { name: '保存 Exploration' }).click();
  await expect(page.getByRole('dialog', { name: '保存 Exploration' })).toBeVisible();
  await capture(page, 'plan_b_current_save_modal_2026-10-05.png');
});

test('captures nested AND OR NOT filter state', async ({ page }) => {
  await routeOrders(page);
  const filters = {
    combinator: 'and',
    rules: [
      { field: 'status', operator: '=', value: 'pending' },
      {
        combinator: 'or',
        not: true,
        rules: [
          { field: 'priority', operator: '>=', value: 4 },
          { field: 'region', operator: '=', value: 'North' },
        ],
      },
    ],
  };
  const params = new URLSearchParams({ view: 'results', objectType: 'Order', filters: JSON.stringify(filters) });
  await mount(page, `/login?${params.toString()}`);
  await expect(page.locator('.oe-query-builder .rule')).toHaveCount(3);
  await capture(page, 'plan_b_current_nested_filters_2026-10-05.png');
});

test('captures the Explore chart layout', async ({ page }) => {
  await routeOrders(page);
  await mount(page, '/login?view=explore&objectType=Order');
  await expect(page.getByRole('img', { name: 'status 当前页分布' })).toBeVisible();
  await expect(page.locator('.oe-chart canvas').first()).toBeVisible();
  await capture(page, 'plan_b_current_explore_layout_2026-10-05.png');
});

test('captures dense Results selection state', async ({ page }) => {
  await routeOrders(page);
  await mount(page);
  await page.getByRole('checkbox', { name: '选择 ORD-1042' }).check();
  await page.getByRole('checkbox', { name: '选择 ORD-1051' }).check();
  await expect(page.getByText('2 selected', { exact: false })).toBeVisible();
  await capture(page, 'plan_b_current_results_multiselect_2026-10-05.png');
});

test('captures Compare with baseline and candidate rows', async ({ page }) => {
  await routeOrders(page);
  await page.route('**/object-query/compare', route => route.fulfill({
    json: {
      added: [{ object_type: 'Order', object_id: 'ORD-1064' }],
      removed: [{ object_type: 'Order', object_id: 'ORD-1038' }],
      retained: [
        {
          object_type: 'Order',
          object_id: 'ORD-1042',
          baseline: { status: 'pending', priority: 5 },
          candidate: { status: 'approved', priority: 5 },
        },
      ],
    },
  }));
  await mount(page, '/login?view=results&objectType=Order&dataView=view-baseline');
  const analysis = page.getByRole('region', { name: '完整统计与 Compare' });
  await analysis.getByRole('button', { name: 'Compare', exact: true }).click();
  await analysis.getByLabel('聚合属性').selectOption('status');
  await analysis.getByLabel('比较视图').fill('view-candidate');
  await analysis.getByRole('button', { name: '运行比较' }).click();
  await expect(analysis.getByLabel('集合比较结果')).toBeVisible();
  await capture(page, 'plan_b_current_compare_2026-10-05.png');
});

test('captures Search Around pivot with traversal context', async ({ page }) => {
  await routeOrders(page);
  await mount(page);
  await page.getByLabel('Search Around', { exact: true }).selectOption('0');
  await expect(page.getByLabel('对象类型', { exact: true })).toHaveValue('Supplier');
  await expect(page.getByLabel('Traversal path')).toContainText('SUPPLIED_BY');
  await expect(page.getByLabel('Traversal path')).toContainText('Supplier');
  await expect(page.getByRole('button', { name: 'SUP-201' })).toBeVisible();
  await capture(page, 'plan_b_current_pivot_2026-10-05.png');
});

test('captures Action modal preview before live commit', async ({ page }) => {
  await routeOrders(page);
  await page.route('**/ontologies/repair/action-runs*', route => route.fulfill({ json: [] }));
  await page.route('**/ontologies/repair/actions', route => route.fulfill({
    json: [{
      id: 'approve',
      name: 'Approve order',
      target_entity_type: 'Order',
      enabled: true,
      status: 'published',
      parameters: [
        { name: 'status', type: 'string', required: true },
        { name: 'review_note', type: 'string', required: false },
      ],
    }],
  }));
  await page.route('**/ontologies/repair/actions/approve/preview', route => route.fulfill({
    json: {
      target: 'live',
      edits: [
        { op: 'invoke_action' },
        { op: 'set_property', property: 'status', value: 'approved' },
        { op: 'set_property', property: 'review_note', value: 'Capacity verified' },
      ],
    },
  }));
  await page.route('**/ontologies/repair/actions/approve/run', route => route.fulfill({
    json: { status: 'completed', target: 'live' },
  }));
  await mount(page);
  await page.getByRole('button', { name: 'ORD-1042' }).click();
  await page.getByRole('button', { name: 'Actions' }).click();
  const dialog = page.getByRole('dialog', { name: '执行对象 Action' });
  await dialog.getByRole('combobox', { name: 'Action' }).selectOption('approve');
  await dialog.getByRole('textbox', { name: 'status' }).fill('approved');
  await dialog.getByRole('textbox', { name: 'review_note' }).fill('Capacity verified');
  await dialog.getByRole('button', { name: '预览' }).click();
  await expect(dialog.getByText('将提交 2 项编辑到 live')).toBeVisible();
  await capture(page, 'plan_b_current_action_modal_2026-10-05.png');
  await dialog.getByRole('button', { name: '确认提交' }).click();
  await expect(dialog).toBeHidden();
  await expect(page.getByRole('status').filter({ hasText: 'Action 已提交到 live。' })).toBeVisible();
  await capture(page, 'plan_b_current_action_success_2026-10-05.png');
});

test('captures actionable Action conflict feedback', async ({ page }) => {
  await routeOrders(page);
  await page.route('**/ontologies/repair/action-runs*', route => route.fulfill({ json: [] }));
  await page.route('**/ontologies/repair/actions', route => route.fulfill({
    json: [{
      id: 'approve',
      name: 'Approve order',
      target_entity_type: 'Order',
      enabled: true,
      status: 'published',
      parameters: [],
    }],
  }));
  await page.route('**/ontologies/repair/actions/approve/preview', route => route.fulfill({
    json: {
      target: 'live',
      edits: [{ op: 'invoke_action' }, { op: 'set_property', property: 'status', value: 'approved' }],
    },
  }));
  await page.route('**/ontologies/repair/actions/approve/run', route => route.fulfill({
    status: 409,
    json: { detail: { code: 'edit_conflict', message: 'status 已被其他用户修改，请刷新对象后重试' } },
  }));
  await mount(page);
  await page.getByRole('button', { name: 'ORD-1042' }).click();
  await page.getByRole('button', { name: 'Actions' }).click();
  const dialog = page.getByRole('dialog', { name: '执行对象 Action' });
  await dialog.getByRole('combobox', { name: 'Action' }).selectOption('approve');
  await dialog.getByRole('button', { name: '预览' }).click();
  await dialog.getByRole('button', { name: '确认提交' }).click();
  await expect(dialog.getByRole('alert')).toContainText('status 已被其他用户修改，请刷新对象后重试');
  await capture(page, 'plan_b_current_action_conflict_2026-10-05.png');
});

test('captures responsive narrow layout', async ({ page }) => {
  await routeOrders(page);
  await mount(page);
  await page.setViewportSize({ width: 390, height: 844 });
  await expect(page.getByRole('button', { name: 'ORD-1042' })).toBeVisible();
  await capture(page, 'plan_b_current_narrow_2026-10-05.png');
});
