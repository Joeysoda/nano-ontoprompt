import { test, expect } from '@playwright/test';

const ontologyId = process.env.PLAN_B_ONTOLOGY_ID;
const objectId = process.env.PLAN_B_OBJECT_ID;
const screenshotPath = process.env.PLAN_B_SCREENSHOT_PATH;

test('real JWT Object Panel renders persisted data after Docker restart', async ({ page }) => {
  test.skip(!ontologyId || !objectId, 'Requires a disposable Plan B persistence fixture');

  await page.goto('/login');
  await page.locator('input[name="username"]').fill('admin');
  await page.locator('input[name="password"]').fill('admin123');
  await page.locator('button[type="submit"]').click();
  await expect(page).toHaveURL(/\/overview$/);

  await page.goto(`/ontologies/${ontologyId}?tab=objects&type=Order&view=results`);
  const explorer = page.getByRole('region', { name: 'Object Explorer' });
  await expect(explorer).toBeVisible();
  await explorer.getByRole('button', { name: objectId }).click();

  const panel = page.getByRole('complementary', { name: `${objectId} 对象预览` });
  await expect(panel).toBeVisible();
  await expect(panel.getByText('Status', { exact: true }).first()).toBeVisible();
  await expect(panel.getByText('reviewing', { exact: true }).first()).toBeVisible();

  await panel.getByRole('button', { name: '完整对象视图' }).click();
  const dialog = page.getByRole('dialog', { name: `${objectId} 完整对象视图` });
  await expect(dialog.getByRole('button', { name: 'Configured v1' })).toBeVisible();
  await expect(dialog.getByText('reviewing', { exact: true })).toBeVisible();

  if (screenshotPath) await page.screenshot({ path: screenshotPath, fullPage: true });
});
