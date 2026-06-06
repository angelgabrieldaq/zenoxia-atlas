const { test, expect } = require('@playwright/test');

test('Atlas frontend carga y muestra el tablero', async ({ page }) => {
  await page.goto('http://127.0.0.1:5173', { waitUntil: 'networkidle' });

  await expect(page).toHaveTitle(/Atlas/);
  await expect(page.locator('.meta-title', { hasText: 'Atlas · Zenoxia' })).toBeVisible();
  await expect(page.locator('#board')).toBeVisible();
  await expect(page.locator('.topbar')).toBeVisible();
});
