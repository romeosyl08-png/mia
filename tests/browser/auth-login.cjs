const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const origin = 'http://127.0.0.1:8000';
const output = '/tmp/2ai-playwright-audit';
fs.mkdirSync(output, {recursive: true});
let browser;

(async () => {
  browser = await chromium.launch({
    executablePath: '/opt/google/chrome/chrome',
    headless: true,
    args: ['--no-sandbox'],
  });
  const context = await browser.newContext({viewport: {width: 1280, height: 900}});
  await context.route('https://telegram.org/js/**', route => route.fulfill({
    status: 200,
    contentType: 'application/javascript',
    body: '',
  }));
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => {
    if (request.failure()?.errorText !== 'net::ERR_ABORTED') errors.push(request.failure()?.errorText);
  });

  await page.goto(`${origin}/auth/`, {waitUntil: 'domcontentloaded'});
  const loginPassword = page.locator('#id_login_password');
  await loginPassword.fill('mot-de-passe-test');
  await page.locator('[data-password-target="id_login_password"]').click();
  assert.equal(await loginPassword.getAttribute('type'), 'text');
  assert.equal(await page.locator('[data-password-target="id_login_password"]').innerText(), 'Masquer');
  await page.locator('[data-password-target="id_login_password"]').click();
  assert.equal(await loginPassword.getAttribute('type'), 'password');

  await page.locator('#waToggle').click();
  assert.ok(await page.locator('#waPanel').isVisible());
  assert.match(await page.locator('#waPanel').innerText(), /indisponible pour le moment/i);

  await page.locator('#tabSignup').click();
  for (const id of ['id_signup_password1', 'id_signup_password2']) {
    await page.locator(`#${id}`).fill('inscription-test');
    await page.locator(`[data-password-target="${id}"]`).click();
    assert.equal(await page.locator(`#${id}`).getAttribute('type'), 'text');
  }

  const telegramAuthUrl = await page.locator('#telegram-login-wrap-signup script[data-auth-url]').getAttribute('data-auth-url');
  assert.ok(telegramAuthUrl.startsWith(`${origin}/telegram/auth/`), telegramAuthUrl);
  assert.ok(await page.locator('#panelSignup a.google').getAttribute('href'));
  await page.waitForTimeout(5100);
  assert.ok(await page.locator('#telegram-login-wrap-signup .telegram-fallback').isVisible());
  assert.ok(await page.locator('#telegram-login-wrap-signup .telegram-loading').isHidden());
  const layout = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
  assert.ok(layout.scroll <= layout.width + 1, JSON.stringify(layout));
  await page.screenshot({path: `${output}/auth-password-social.png`, fullPage: true});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({passwordVisibility: true, whatsappNotice: true, telegramAbsoluteCallback: true, telegramFallback: true, layout}));
})().finally(async () => { if (browser) await browser.close(); });
