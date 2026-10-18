import { test, expect } from '@playwright/test';
import { signInAsAdmin } from './e2e-auth';

/**
 * The outer evaluation page is a cross-agent board now: case editing and running
 * stay in the agent workspace, so the board must only aggregate and route there.
 */
test('quality board aggregates agents, review queue and regressions', async ({ page, request }) => {
  test.setTimeout(60_000);
  const errors: string[] = [];
  page.on('pageerror', error => errors.push(error.message));
  await signInAsAdmin(page, request);
  await page.goto('/#/evaluation');
  await expect(page.locator('.quality-board')).toBeVisible();
  await expect(page.locator('.quality-metrics .metric')).toHaveCount(6);
  await expect(page.locator('.quality-board')).toContainText('质量概览');
  await expect(page.locator('.quality-board')).toContainText('待复核队列');
  await expect(page.locator('.quality-board')).toContainText('回归用例');
  await expect(page.locator('.quality-filters button.selected')).toContainText('待复核');
  await page.locator('.quality-filters button').filter({ hasText: '全部' }).click();
  await expect(page.locator('.quality-filters button.selected')).toContainText('全部');
  // 处理 routes into the agent that owns the case rather than editing it here.
  await page.locator('.quality-board .text-button').first().click();
  await expect(page).toHaveURL(/#\/agents\/[0-9a-f-]+\/evaluation/);
  expect(errors).toEqual([]);
});
