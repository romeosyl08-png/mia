const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const origin = 'http://127.0.0.1:8000';
const credentials = JSON.parse(fs.readFileSync('/tmp/2ai-playwright-audit/test-account.json', 'utf8'));
let browser;

(async () => {
  browser = await chromium.launch({executablePath:'/opt/google/chrome/chrome', headless:true, args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1280,height:900}});
  const page = await context.newPage();
  const errors = [];
  page.on('pageerror', error => errors.push(error.message));
  page.on('requestfailed', request => {
    if(request.failure()?.errorText !== 'net::ERR_ABORTED') errors.push(request.failure()?.errorText);
  });

  await page.goto(origin + '/auth/?next=/', {waitUntil:'domcontentloaded'});
  await page.locator('#id_login_username').fill(credentials.username);
  await page.locator('#id_login_password').fill(credentials.password);
  await Promise.all([
    page.waitForURL(url => url.pathname === '/'),
    page.locator('#panelLogin button[type=submit]').click(),
  ]);
  await page.waitForFunction(() => document.querySelector('#personaTopSelect')?.options.length > 0);
  await page.route('**/chat/api/media/generate/', route => route.fulfill({
    status: 502,
    contentType: 'application/json',
    body: JSON.stringify({
      error: {type:'media_generation_failed', message:'Le format demandé a été refusé par le fournisseur.'},
      remaining_balance: 12345,
    }),
  }));

  await page.locator('#newConvoBtn').click();
  await page.locator('#imageModeBtn').click();
  await page.locator('#composerInput').fill("Crée un fond d’écran");
  await page.locator('#sendBtn').click();
  await page.waitForFunction(() => document.querySelector('#messages .msg.bot')?.textContent.includes('format demandé'));

  assert.equal(await page.locator('#creditPillText').innerText(), '12 345 jetons');
  assert.equal(await page.locator('#messages .msg-footer').count(), 0);
  assert.match(await page.locator('#messages .msg.bot').innerText(), /format demandé a été refusé/);
  assert.deepEqual(errors, []);
  console.log(JSON.stringify({balanceResynced:true, zeroCounterHidden:true, providerErrorVisible:true}));
})().finally(async () => { if(browser) await browser.close(); });
