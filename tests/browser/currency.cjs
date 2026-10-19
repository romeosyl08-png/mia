const {chromium} = require('playwright');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const origin = 'http://127.0.0.1:8000';
const outputDir = '/tmp/2ai-playwright-audit';
const credentials = JSON.parse(fs.readFileSync(`${outputDir}/test-account.json`, 'utf8'));
let browser;

(async () => {
  browser = await chromium.launch({
    executablePath: '/opt/google/chrome/chrome',
    headless: true,
    args: ['--no-sandbox'],
  });
  const context = await browser.newContext({viewport: {width: 1280, height: 900}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(`JS: ${error.message}`));
  page.on('requestfailed', request => {
    const reason = request.failure()?.errorText;
    if (reason && reason !== 'net::ERR_ABORTED') errors.push(`Réseau: ${request.url()} ${reason}`);
  });

  await page.goto(`${origin}/auth/?next=/recharge/`, {waitUntil: 'domcontentloaded'});
  await page.locator('#id_login_username').fill(credentials.username);
  await page.locator('#id_login_password').fill(credentials.password);
  await Promise.all([
    page.waitForURL(url => url.pathname === '/recharge/'),
    page.locator('#panelLogin button[type=submit]').click(),
  ]);

  async function chooseCurrency(code) {
    await Promise.all([
      page.waitForNavigation({waitUntil: 'domcontentloaded'}),
      page.locator('#currencySelect').selectOption(code),
    ]);
    assert.equal(await page.locator('#currencySelect').inputValue(), code);
    assert.equal(await page.locator('#paymentForm input[name=currency]').inputValue(), code);
    assert.equal((await page.locator('.payment-tag').innerText()).trim(), code);
  }

  await chooseCurrency('EUR');
  assert.match(await page.locator('.recharge-equivalence strong').innerText(), /^0,92 EUR = 10[\s\u202f]000 jetons$/);
  assert.equal(await page.locator('#paymentForm').getAttribute('action'), '/recharge/create-checkout/');
  if(await page.locator('#payment-amount').isEnabled()) {
    await page.locator('#payment-amount').fill('9.20');
    assert.match(await page.locator('#creditsEstimate').innerText(), /100[\s\u202f]000 jetons/);
  }

  await page.goto(`${origin}/tarifs/`, {waitUntil: 'domcontentloaded'});
  const tariffReference = await page.locator('.tariffs-equivalence strong').innerText();
  assert.match(tariffReference, /solde de jetons pour Chat, Bara et Barrassons/);
  assert.doesNotMatch(await page.locator('main').innerText(), /EUR|XOF|USD|FCFA|€/);
  await page.goto(`${origin}/nous/`, {waitUntil: 'domcontentloaded'});
  const aboutReference = await page.locator('.about-capability').filter({hasText: 'Comprendre les tarifs'}).innerText();
  assert.match(aboutReference, /jetons sont la seule monnaie de 2AI/);
  assert.doesNotMatch(aboutReference, /EUR|XOF|USD|FCFA|€/);

  await page.goto(`${origin}/recharge/`, {waitUntil: 'domcontentloaded'});
  await chooseCurrency('XOF');
  assert.equal(await page.locator('#paymentForm').getAttribute('action'), '/recharge/paydunya/');
  assert.match(await page.locator('.recharge-equivalence strong').innerText(), /^600 XOF = 10[\s\u202f]000 jetons$/);

  await page.setViewportSize({width: 390, height: 844});
  await page.reload({waitUntil: 'domcontentloaded'});
  const layout = await page.evaluate(() => ({width: innerWidth, scrollWidth: document.documentElement.scrollWidth}));
  assert.ok(layout.scrollWidth <= layout.width + 1, JSON.stringify(layout));
  await page.screenshot({path: `${outputDir}/currency-mobile.png`, fullPage: true});
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({eur: true, xof: true, persistence: true, noOverflow: true, layout}));
})().finally(async () => { if(browser) await browser.close(); });
