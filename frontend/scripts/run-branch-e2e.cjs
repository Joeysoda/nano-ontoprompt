const { spawnSync } = require('node:child_process');
const path = require('node:path');

// Keep legacy entry points on the same authenticated scenarios and browser
// configuration as the Playwright runner (including traces and failures).
module.exports = function runBranchE2E(feature) {
  const result = spawnSync(process.execPath, [
    require.resolve('@playwright/test/cli'), 'test', 'branch-features.spec.ts',
    '--grep', `^.*${feature}:`, '--workers=1',
    '--output', `test-results/branch-acceptance/${feature.replaceAll(' ', '-').toLowerCase()}`,
  ], {
    cwd: path.resolve(__dirname, '..'),
    stdio: 'inherit',
    env: { ...process.env, PLAYWRIGHT_BASE_URL: process.env.TEST_BASE_URL || process.env.PLAYWRIGHT_BASE_URL || 'http://127.0.0.1:5173' },
  });
  if (result.error) console.error(result.error);
  process.exitCode = result.status ?? 1;
};
