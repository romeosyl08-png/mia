const {chromium} = require('playwright');
const fs = require('node:fs');
const assert = require('node:assert/strict');

const origin = 'http://127.0.0.1:8000';
const credentials = JSON.parse(fs.readFileSync('/tmp/2ai-playwright-audit/test-account.json','utf8'));
assert.ok(credentials.username.startsWith('qa_playwright_'));
let browser;
(async()=>{
  browser = await chromium.launch({executablePath:'/opt/google/chrome/chrome',headless:true,args:['--no-sandbox']});
  const context = await browser.newContext({viewport:{width:1440,height:900}});
  const page = await context.newPage();
  const errors = [], requests = [];
  page.on('pageerror',error=>errors.push(error.message));
  page.on('requestfailed',request=>{if(request.failure()?.errorText!=='net::ERR_ABORTED')errors.push(request.failure()?.errorText);});
  await page.goto(origin+'/auth/?next=/',{waitUntil:'domcontentloaded'});
  await page.locator('#id_login_username').fill(credentials.username);
  await page.locator('#id_login_password').fill(credentials.password);
  await Promise.all([page.waitForURL(url=>url.pathname==='/'),page.locator('#panelLogin button[type=submit]').click()]);
  await page.waitForFunction(()=>document.querySelector('#personaTopSelect')?.options.length>0&&!document.querySelector('#temporaryChatBtn')?.disabled);
  await page.route('**/chat/api/send/',async route=>{
    const body=route.request().postDataJSON(); requests.push(body);
    const done={type:'done',reply:'Réponse temporaire de test.',title:body.content,conversation_id:null,
      prompt_tokens:4,completion_tokens:4,remaining_balance:20000,sources:[],tool_events:[]};
    await route.fulfill({status:200,contentType:'text/event-stream',body:'data: '+JSON.stringify(done)+'\n\n'});
  });

  await page.locator('#temporaryChatBtn').click();
  assert.ok(await page.locator('#temporaryChatControls').isVisible());
  assert.ok(await page.locator('#temporaryMemoryControl').isHidden());
  assert.ok(await page.locator('#temporarySaveControl').isHidden());
  assert.equal(await page.locator('#temporaryPrivacySummary').innerText(),'Sans mémoire · non enregistrée');
  await page.locator('#composerInput').fill('Message direct sans mémoire '+Date.now());
  await page.locator('#sendBtn').click();
  await page.waitForFunction(()=>document.querySelectorAll('#messages .msg.bot').length===1);
  assert.equal(requests[0].temporary,true); assert.equal(requests[0].temporary_memory_persona_id,null);

  await page.locator('#temporaryMemoryBtn').click();
  assert.ok(await page.locator('#temporaryMemoryControl').isVisible());
  assert.ok(await page.locator('#temporarySaveControl').isHidden());
  const persona = await page.locator('#temporaryMemoryPersona option').last().getAttribute('value');
  await page.locator('#temporaryMemoryPersona').selectOption(persona);
  assert.match(await page.locator('#temporaryPrivacySummary').innerText(),/^Mémoire de /);
  await page.locator('#composerInput').fill('Message avec mémoire facultative '+Date.now());
  await page.locator('#sendBtn').click();
  await page.waitForFunction(()=>document.querySelectorAll('#messages .msg.bot').length===2);
  assert.equal(requests[1].temporary_memory_persona_id,Number(persona));

  await page.locator('#temporarySaveBtn').click();
  assert.ok(await page.locator('#temporaryMemoryControl').isHidden());
  assert.ok(await page.locator('#temporarySaveControl').isVisible());
  await page.locator('#temporarySavePersona').selectOption(persona);
  await page.waitForFunction(()=>!document.querySelector('#saveTemporaryConversationBtn').disabled);
  await page.locator('#saveTemporaryConversationBtn').click();
  await page.locator('#temporaryChatControls').waitFor({state:'hidden'});
  await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(()=>document.querySelector('#personaTopSelect')?.options.length>0&&!document.querySelector('#temporaryChatBtn')?.disabled);
  assert.equal(await page.locator('#messages .msg').count(),4);

  await page.setViewportSize({width:390,height:844});
  await page.locator('#temporaryChatBtn').click();
  assert.ok(await page.locator('#temporaryMemoryControl').isHidden());
  assert.ok(await page.locator('#temporarySaveControl').isHidden());
  const layout = await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));
  assert.ok(layout.scroll<=layout.width+1,JSON.stringify(layout));
  await page.screenshot({path:'/tmp/2ai-playwright-audit/temporary-actions-mobile.png',fullPage:true});
  assert.deepEqual(errors,[]);
  console.log(JSON.stringify({directWithoutMemory:true,memoryButton:true,saveButton:true,actualSave:true,mobile:layout}));
})().finally(async()=>{if(browser)await browser.close();});
