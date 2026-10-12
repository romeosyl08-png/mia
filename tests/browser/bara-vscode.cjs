const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const origin = 'http://127.0.0.1:8000';
const outputDir = '/tmp/2ai-playwright-audit';
fs.mkdirSync(outputDir, {recursive: true});
const credentialsPath = `${outputDir}/test-account.json`;
const newAccount = !fs.existsSync(credentialsPath);
const credentials = newAccount ? {
  username: `qa_playwright_bara_${Date.now()}`,
  email: `qa.bara.${Date.now()}@example.test`,
  password: 'Audit!2AI-2026-Test#9',
} : JSON.parse(fs.readFileSync(credentialsPath, 'utf8'));
assert.ok(credentials.username.startsWith('qa_playwright_'));

let browser;
(async () => {
  browser = await chromium.launch({
    executablePath: '/opt/google/chrome/chrome',
    headless: true,
    args: ['--no-sandbox'],
  });
  const context = await browser.newContext({viewport: {width: 1440, height: 900}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(`JS: ${error.message}`));
  page.on('requestfailed', request => {
    const reason = request.failure()?.errorText;
    if (reason && reason !== 'net::ERR_ABORTED') errors.push(`Réseau: ${request.url()} ${reason}`);
  });

  await page.goto(`${origin}/auth/?next=/bara/`, {waitUntil: 'domcontentloaded'});
  if (newAccount) {
    await page.locator('#tabSignup').click();
    await page.locator('#id_signup_username').fill(credentials.username);
    await page.locator('#id_signup_email').fill(credentials.email);
    await page.locator('#id_signup_password1').fill(credentials.password);
    await page.locator('#id_signup_password2').fill(credentials.password);
    await Promise.all([
      page.waitForURL(url => url.pathname !== '/auth/' && url.pathname !== '/signup/'),
      page.locator('#panelSignup button[type=submit]').click(),
    ]);
    fs.writeFileSync(credentialsPath, JSON.stringify(credentials), {mode: 0o600});
    await page.goto(`${origin}/bara/`, {waitUntil: 'domcontentloaded'});
  } else {
    await page.locator('#id_login_username').fill(credentials.username);
    await page.locator('#id_login_password').fill(credentials.password);
    await Promise.all([
      page.waitForURL(url => url.pathname === '/bara/'),
      page.locator('#panelLogin button[type=submit]').click(),
    ]);
  }

  assert.ok(await page.locator('.bara-home').isVisible());
  let workspace = page.locator('.ws-card').first();
  if (await workspace.count() === 0) {
    await page.locator('.ws-create-form input[name=title]').fill('Bara interface QA');
    await Promise.all([
      page.waitForURL(url => /\/bara\/\d+\/c\/\d+\/$/.test(url.pathname)),
      page.locator('.ws-create-form button[type=submit]').click(),
    ]);
  } else {
    await Promise.all([
      page.waitForURL(url => /\/bara\/\d+\/c\/\d+\/$/.test(url.pathname)),
      workspace.click(),
    ]);
  }

  await page.locator('.bara-workbench').waitFor();
  await page.locator('.editor-tab.active').waitFor();
  assert.ok(await page.locator('.bara-activity').isVisible());
  assert.ok(await page.locator('.bara-sidebar').isVisible());
  assert.ok(await page.locator('.bara-statusbar').isVisible());
  assert.equal(await page.locator('#toggle-workbench-sidebar').getAttribute('aria-expanded'), 'true');

  await page.locator('#toggle-workbench-sidebar').click();
  await page.waitForFunction(() => document.querySelector('.bara-workbench').classList.contains('sidebar-collapsed'));
  assert.equal(await page.locator('#toggle-workbench-sidebar').getAttribute('aria-expanded'), 'false');
  await page.locator('#toggle-workbench-sidebar').click();
  await page.waitForFunction(() => !document.querySelector('.bara-workbench').classList.contains('sidebar-collapsed'));

  await page.locator('[data-workbench-target="workspace-tools-panel"]').click();
  assert.equal(await page.locator('#workspace-tools-panel').getAttribute('open'), '');
  await page.locator('[data-workbench-target="workspace-files-panel"]').click();
  assert.ok(await page.locator('#workspace-files-panel').isVisible());
  const desktop = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
  assert.ok(desktop.scroll <= desktop.width + 1, JSON.stringify(desktop));
  await page.screenshot({path: `${outputDir}/bara-vscode-desktop.png`, fullPage: true});

  await page.setViewportSize({width: 390, height: 844});
  await page.waitForTimeout(150);
  assert.ok(await page.locator('.bara-activity').isVisible());
  assert.ok(await page.locator('.bara-statusbar').isVisible());
  const mobile = await page.evaluate(() => ({width: innerWidth, scroll: document.documentElement.scrollWidth}));
  assert.ok(mobile.scroll <= mobile.width + 1, JSON.stringify(mobile));
  await page.locator('#toggle-workbench-sidebar').click();
  assert.equal(await page.locator('#toggle-workbench-sidebar').getAttribute('aria-expanded'), 'false');
  await page.locator('#toggle-workbench-sidebar').click();
  await page.screenshot({path: `${outputDir}/bara-vscode-mobile.png`, fullPage: true});

  assert.deepEqual(errors, []);
  console.log(JSON.stringify({desktop, mobile, activityNavigation: true, sidebarToggle: true}));
})().finally(async () => { if (browser) await browser.close(); });
