import { expect, test } from '@playwright/test'

// Renders every page of an installed app and screenshots it. WORKSHOP_APP / WORKSHOP_ONTOLOGY come from the seed script output.
const app = process.env.WORKSHOP_APP ?? ''
const ontology = process.env.WORKSHOP_ONTOLOGY ?? ''
test.skip(!app || !ontology, 'needs a seeded app')

test('every page of the installed app renders bound data', async ({ page }) => {
  await page.setViewportSize({ width: 1440, height: 1000 })
  for (const [id, expectText] of [['cockpit', 'Projected Revenue'], ['demand', 'Demand Event Views'], ['coverage', 'Requirements'], ['actions', 'Suggestions']]) {
    await page.goto(`/apps/workshop/${app}?ontology=${ontology}&page=${id}`)
    await expect(page.getByTestId('workshop-app')).toContainText(expectText, { timeout: 30_000 })
    await expect(page.getByText('数据加载中…')).toHaveCount(0, { timeout: 30_000 })
    await page.screenshot({ path: test.info().outputPath(`${id}.png`), fullPage: true })
  }
})
