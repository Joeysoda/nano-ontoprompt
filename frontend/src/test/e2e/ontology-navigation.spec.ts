import { test, expect, type Page } from '@playwright/test';

const ONT = 'nav-ont';
const rule = { id: 'rule-1', api_name: 'vibration_drift', display_name: '振动漂移', description: '', status: 'active', current_version: 1, owner_id: 'test-user',
  schedule: { interval_minutes: 60, source: 'latest_view', last_scheduled_at: null, error: null } };
const alerts = [1, 2, 3].map(i => ({ id: `a${i}`, rule_id: 'rule-1', rule_version_id: 'v1', status: 'open', severity: i === 1 ? 'critical' : 'high',
  subject: { object_type: 'Machine', object_id: `machine-00${i}` }, window_start: '2015-01-02T01:45:00+00:00', window_end: '2015-01-02T03:00:00+00:00',
  properties: {}, evidence: { peak: 50 + i, breaching_windows: 2 }, suggested_action: null, occurrence_count: i, recovered_at: null,
  resolution_note: '', created_at: '2026-10-18T00:00:00+00:00' }));

async function mount(page: Page, path = `/ontologies/${ONT}`) {
  const posts: string[] = [];
  await page.setViewportSize({ width: 1440, height: 900 });
  await page.addInitScript(() => {
    localStorage.setItem('token', 'nav-test');
    localStorage.setItem('auth-store', JSON.stringify({ state: { token: 'nav-test', user: { id: 'test-user', username: 'admin', role: 'admin' } }, version: 0 }));
  });
  await page.route(url => url.pathname.startsWith('/api/'), route => {
    const url = new URL(route.request().url());
    const p = url.pathname;
    const json = (body: unknown) => route.fulfill({ contentType: 'application/json', body: JSON.stringify(body) });
    if (route.request().method() === 'POST') posts.push(p);
    if (p === `/api/v1/ontologies/${ONT}`) return json({ data: { id: ONT, name: '设备运维', status: 'published', data_class: 'structured', domain: 'manufacturing' } });
    if (p.endsWith('/alerts') && url.searchParams.get('status') === 'open' && url.searchParams.get('limit') === '1') return json({ alerts: alerts.slice(0, 1), total: 3 });
    if (p.endsWith('/alerts')) return json({ alerts, total: 3 });
    if (p.includes('/alerts/')) return json({ ...alerts[0], status: 'acknowledged' });
    if (p.endsWith('/rules')) return json({ rules: [rule] });
    if (p.endsWith('/rules/rule-1')) return json({ rule, versions: [{ version: 1, logic: {} }], proposals: [] });
    if (p.endsWith('/evaluations')) return json({ evaluations: [] });
    if (p.endsWith('/object-query/catalog')) return json({ metadata_digest: 'm', types: [{ kind: 'object', api_name: 'Machine', properties: [] }], links: [] });
    if (p.endsWith('/object-query/load')) return json({ objects: [], completeness: 'complete', status: 'empty', page: { returned: 0, has_more: false } });
    if (p.endsWith('/object-sets/resources')) return json({ resources: [] });
    return json({ data: [], items: [] });
  });
  await page.goto(path);
  return posts;
}

test('ontology opens on the Object Explorer with grouped Use / Build / Data navigation', async ({ page }) => {
  await mount(page);
  const nav = page.getByRole('navigation', { name: '本体导航' });
  for (const group of ['使用', '构建', '数据与治理']) await expect(nav.getByText(group, { exact: true })).toBeVisible();
  await expect(nav.getByRole('button', { name: /对象浏览器/ })).toHaveAttribute('aria-current', 'page');
  await expect(nav.getByLabel('3 条待处理告警')).toBeVisible();
  await expect(page.getByRole('heading', { name: '设备运维' })).toBeVisible();
  await page.screenshot({ path: 'test-results/nav-objects.png', fullPage: true });
});

test('alert center lists, filters and acknowledges; rules page manages schedule and evaluation', async ({ page }) => {
  const posts = await mount(page, `/ontologies/${ONT}?tab=alerts`);
  const center = page.getByRole('region', { name: '告警中心' });
  await expect(center.getByTestId('alert-row')).toHaveCount(3);
  await center.getByTestId('alert-row').first().getByRole('button', { name: '确认' }).click();
  await expect.poll(() => posts.some(p => p.endsWith('/alerts/a1:acknowledge'))).toBeTruthy();
  await page.screenshot({ path: 'test-results/nav-alerts.png', fullPage: true });

  await page.getByRole('navigation', { name: '本体导航' }).getByRole('button', { name: /运行规则/ }).click();
  await expect(page).toHaveURL(/tab=rules/);
  const rules = page.getByRole('region', { name: '运行规则' });
  await expect(rules.getByRole('heading', { name: '振动漂移' })).toBeVisible();
  await expect(rules.getByLabel('评估间隔')).toHaveValue('60');
  await rules.getByRole('button', { name: '立即评估' }).click();
  await expect.poll(() => posts.some(p => p.endsWith('/rules/rule-1/evaluate:latest'))).toBeTruthy();
  await page.screenshot({ path: 'test-results/nav-rules.png', fullPage: true });
});

test('old tab links still work and scenario studies open from the Use group', async ({ page }) => {
  await mount(page, `/ontologies/${ONT}?tab=logic-assets`);
  const nav = page.getByRole('navigation', { name: '本体导航' });
  await expect(nav.getByRole('button', { name: /函数绑定/ })).toHaveAttribute('aria-current', 'page');
  await nav.getByRole('button', { name: /情景研究/ }).click();
  await expect(page).toHaveURL(new RegExp(`/ontologies/${ONT}/scenario-studies`));
});

test('global sidebar groups pages and merges the three scenario entries', async ({ page }) => {
  await mount(page);
  const main = page.getByRole('navigation', { name: '主导航' });
  for (const group of ['工作台', '本体', '数据', '情景决策', '系统']) await expect(main.getByText(group, { exact: true })).toBeVisible();
  await expect(main.getByRole('link', { name: /情景与推演/ })).toHaveCount(1);
  await expect(main.getByRole('link', { name: /自定义研究/ })).toHaveCount(0);  // collapsed outside its section
  await main.getByRole('button', { name: '展开情景与推演菜单' }).click();
  for (const sub of ['自定义研究', '通用情景', '供应商推演演示']) await expect(main.getByRole('link', { name: new RegExp(sub) })).toBeVisible();
});
