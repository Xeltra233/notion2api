const { chromium } = require('playwright');

async function main() {
  const baseUrl = process.env.TEST_BASE_URL || 'http://127.0.0.1:8010';
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();

  try {
    await page.goto(baseUrl, { waitUntil: 'networkidle' });

    await page.locator('#adminConnectionSettingsDetails').evaluate((el) => {
      el.open = true;
    });
    await page.fill('#baseUrlInput', baseUrl);
    await page.fill('#apiKeyInput', 'test-server-key');

    await page.fill('#adminUsernameInput', 'admin');
    await page.fill('#adminPasswordInput', 'bootstrap-admin-password');
    await page.click('#adminLoginBtn');

    await page.waitForTimeout(1500);

    const title = await page.locator('#adminAccessStatusTitle').textContent();
    const detail = await page.locator('#adminAccessStatusDetail').textContent();
    const runtimeStatus = await page.locator('#runtimeSectionStatus').textContent();
    const overviewStatus = await page.locator('#overviewSectionStatus').textContent();

    const runtimeButtonDisabled = await page.locator('#runtimeLoadBtn').isDisabled();

    await page.fill('#adminPasswordInput', 'bootstrap-admin-password');
    await page.fill('#adminNewPasswordInput', 'bootstrap-admin-password-rotated');
    await page.click('#adminUpdateCredentialsBtn');

    await page.waitForTimeout(2000);

    const titleAfter = await page.locator('#adminAccessStatusTitle').textContent();
    const runtimeStatusAfter = await page.locator('#runtimeSectionStatus').textContent();
    const runtimeButtonDisabledAfter = await page.locator('#runtimeLoadBtn').isDisabled();

    console.log(JSON.stringify({
      title,
      detail,
      runtimeStatus,
      overviewStatus,
      runtimeButtonDisabled,
      titleAfter,
      runtimeStatusAfter,
      runtimeButtonDisabledAfter,
    }));
  } finally {
    await browser.close();
  }
}

main().catch((error) => {
  console.error(error);
  process.exit(1);
});
