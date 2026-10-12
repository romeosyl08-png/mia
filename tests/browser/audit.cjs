const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');

const origin = 'http://127.0.0.1:8000';
const output = '/tmp/2ai-playwright-audit';
fs.mkdirSync(output, {recursive: true});
const credentialsPath = path.join(output, 'test-account.json');
const previousReportPath = path.join(output, 'report.json');
const previousReport = fs.existsSync(previousReportPath) ? JSON.parse(fs.readFileSync(previousReportPath)) : null;
const reuseAccount = !fs.existsSync(credentialsPath) && previousReport?.account?.startsWith('qa_playwright_')
  && previousReport.cases?.some(item => item.name.includes('création du compte'))
  && !previousReport.network?.some(item => item.path === '/signup/' && item.status === 500);
const credentials = fs.existsSync(credentialsPath) ? JSON.parse(fs.readFileSync(credentialsPath)) : reuseAccount ? {
  username:previousReport.account,email:`qa.${previousReport.account.split('_').at(-1)}@example.test`,password:'Audit!2AI-2026-Test#9'
} : {
  username: `qa_playwright_${Date.now()}`, email: `qa.${Date.now()}@example.test`, password: 'Audit!2AI-2026-Test#9'
};
const report = {origin, started: new Date().toISOString(), account: credentials.username,
  cases: [], javascript: [], console: [], network: [], blocked: [], screenshots: [], simulation: 'Chat generation responses are simulated. Account, personas, saving conversations and workspace operations use the actual Django application.'};
const slug = name => name.replace(/[^a-z0-9]+/gi, '-').toLowerCase();
let page, browser, context, currentCase = '', chatRequests = [];
const testPhase = process.argv[2] || 'all';
function observe(p) {
  p.on('pageerror', error => report.javascript.push({case: currentCase, error: error.message}));
  p.on('console', message => {if(message.type() === 'error') report.console.push({case: currentCase, error: message.text().slice(0,500)});});
  p.on('requestfailed', request => report.network.push({case: currentCase, path: new URL(request.url()).pathname, failure: request.failure()?.errorText}));
  p.on('response', response => {if(response.status() >= 400) report.network.push({case: currentCase, path: new URL(response.url()).pathname, status: response.status()});});
}
async function screenshot(name) {
  const file = path.join(output, slug(name) + '.png');
  await page.screenshot({path: file, fullPage: true}); report.screenshots.push(file);
}
async function check(name, run) {
  currentCase = name;
  try {const detail = await run(); report.cases.push({name, status: 'passed', detail});console.log('PASS',name);}
  catch(error) {report.cases.push({name,status:'failed',error:error.message.slice(0,1100)});console.log('FAIL',name,error.message.slice(0,350));await screenshot(name).catch(()=>{});}
  fs.writeFileSync(path.join(output,'report.json'), JSON.stringify(report,null,2));
}
async function visit(url) {const response = await page.goto(origin+url,{waitUntil:'domcontentloaded',timeout:45000}); assert.ok(response.status()<400,`${url}: HTTP ${response.status()}`);}
async function readyChat() {await page.waitForFunction(()=>document.querySelector('#personaTopSelect')?.options.length>0 && !document.querySelector('#temporaryChatBtn')?.disabled,{},{timeout:45000});}
async function layout() {
  const result = await page.evaluate(()=>({width:innerWidth,scrollWidth:document.documentElement.scrollWidth,height:innerHeight}));
  assert.ok(result.scrollWidth <= result.width+1,`Horizontal overflow: ${JSON.stringify(result)}`); return result;
}
async function login(p, identifier=credentials.username) {
  await p.goto(origin+'/auth/?next=/',{waitUntil:'domcontentloaded'});
  await p.locator('#id_login_username').fill(identifier);
  await p.locator('#id_login_password').fill(credentials.password);
  await Promise.all([p.waitForURL(url=>url.pathname==='/',{waitUntil:'domcontentloaded',timeout:45000}),p.locator('#panelLogin button[type=submit]').click({noWaitAfter:true})]);
}

(async()=>{
  browser=await chromium.launch({executablePath:'/opt/google/chrome/chrome',headless:true,args:['--no-sandbox']});
  context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin && (/\/delete\/$/.test(url.pathname) || ['/recharge/create-checkout/','/recharge/paydunya/'].includes(url.pathname))) {
      report.blocked.push({path:url.pathname,method:route.request().method()});
      return route.fulfill({status:403,contentType:'application/json',body:JSON.stringify({error:'Action excluded from the test'})});
    }
    return route.continue();
  });
  page=await context.newPage();observe(page);page.setDefaultTimeout(15000);page.setDefaultNavigationTimeout(45000);
  if(testPhase==='screenshots') {
    assert.ok(fs.existsSync(credentialsPath),'Use the previously created test account.');
    await login(page);await page.setViewportSize({width:390,height:844});
    await visit('/api/');
    let file=path.join(output,'api-mobile-after.png');await page.screenshot({path:file,fullPage:false});report.screenshots.push(file);
    const last=previousReport.cases.find(item=>/^Navigation 390 — \/bara\/\d+\/c\/\d+\/$/.test(item.name));
    if(last){await visit(last.name.split(' — ')[1]);file=path.join(output,'bara-mobile-after.png');await page.screenshot({path:file,fullPage:true});report.screenshots.push(file);}
    previousReport.screenshots.push(...report.screenshots);fs.writeFileSync(previousReportPath,JSON.stringify(previousReport,null,2));
    console.log(JSON.stringify({screenshots:report.screenshots}));return;
  }
  if(!fs.existsSync(credentialsPath) && !reuseAccount) {
    await check('Inscription — mots de passe incohérents',async()=>{
      await visit('/auth/?next=/');await page.locator('#tabSignup').click();
      await page.locator('#id_signup_username').fill(credentials.username);
      await page.locator('#id_signup_email').fill(credentials.email);
      await page.locator('#id_signup_password1').fill(credentials.password);
      await page.locator('#id_signup_password2').fill('Different!Password2');
      await Promise.all([page.waitForURL('**/signup/**'),page.locator('#panelSignup button[type=submit]').click()]);
      assert.ok(await page.locator('#panelSignup .errorlist').isVisible());
      assert.ok(await page.locator('#panelSignup').isVisible());
    });
    await check('Inscription — création du compte de test',async()=>{
      await page.locator('#id_signup_password1').fill(credentials.password);
      await page.locator('#id_signup_password2').fill(credentials.password);
      await Promise.all([page.waitForURL(url=>url.pathname==='/',{waitUntil:'domcontentloaded',timeout:45000}),page.locator('#panelSignup button[type=submit]').click()]);
      fs.writeFileSync(credentialsPath,JSON.stringify(credentials),{mode:0o600});await readyChat();
      assert.ok(await page.locator('#personaTopSelect option').count()>=4);
    });
  } else {await login(page);fs.writeFileSync(credentialsPath,JSON.stringify(credentials),{mode:0o600});}
  assert.ok(fs.existsSync(credentialsPath),'A test account is required for the rest of the audit.');
  if(testPhase==='all') {
    const authContext=await browser.newContext();const auth=await authContext.newPage();observe(auth);auth.setDefaultTimeout(15000);auth.setDefaultNavigationTimeout(45000);
    await check('Connexion — refus de mot de passe incorrect',async()=>{
      await auth.goto(origin+'/auth/');await auth.locator('#id_login_username').fill(credentials.username);
      await auth.locator('#id_login_password').fill('Wrong!Password');
      await Promise.all([auth.waitForURL('**/login/**'),auth.locator('#panelLogin button[type=submit]').click()]);
      assert.ok(await auth.locator('#panelLogin .errorlist').isVisible());
    });
    await check('Connexion — adresse e-mail',async()=>{await login(auth,credentials.email);assert.equal(new URL(auth.url()).pathname,'/');});
    await check('Connexion — nom utilisateur',async()=>{await login(auth);assert.equal(new URL(auth.url()).pathname,'/');});
    await authContext.close();
  }
  await check('Chat — boutons, modèles et catégories',async()=>{
    await visit('/');await readyChat();await page.getByRole('button',{name:'Choisir une IA',exact:true}).click();
    assert.ok(await page.locator('#modelPickerDialog').isVisible());
    const count=await page.locator('#modelPickerList .model-picker-item[data-model-id]:not(#localModelDialogBtn)').count();
    await page.locator('#modelPickerSearch').fill('qa-no-model-match');
    assert.ok(await page.locator('#modelPickerNoResults').isVisible());
    await page.locator('#modelPickerSearch').fill('');
    await page.locator('#modelTaskCategory').selectOption('programming');
    if(count) await page.locator('#modelPickerList .model-picker-item[data-model-id]:not(#localModelDialogBtn)').first().click();
    else await page.locator('#closeModelPickerBtn').click();
    await page.locator('#generationControlsBtn').click();assert.ok(await page.locator('#generationControlsPanel').isVisible());
    await page.locator('#temperatureControl').fill('0.4');await page.locator('#generationControlsBtn').click();
    await page.locator('#attachBtn').click();assert.ok(await page.locator('#composerPlusMenu').isVisible());
    await page.keyboard.press('Escape');assert.ok(await page.locator('#composerPlusMenu').isHidden());
    return {models:count};
  });
  await check('Personas — création et renommage',async()=>{
    await visit('/');await readyChat();const previous=await page.locator('#personaTopSelect option').count();
    await page.locator('#newPersonaBtn').click();
    await page.waitForFunction(n=>document.querySelector('#personaTopSelect').options.length>n,previous);
    await page.locator('#renamePersonaBtn').click();await page.locator('#personaRenameInput').fill('Persona Playwright — test');
    await page.locator('#personaRenameForm button[type=submit]').click();
    await page.waitForFunction(()=>document.querySelector('#activePersonaName').textContent.includes('Persona Playwright'));
    await page.reload();await readyChat();assert.ok((await page.locator('#personaTopSelect').innerText()).includes('Persona Playwright'));
  });
  await check('Personas — mémoire et réglages enregistrés',async()=>{
    await visit('/customize/');await page.locator('#personaSelect option').last().waitFor({state:'attached'});
    const value=await page.locator('#personaSelect option').last().getAttribute('value');
    await page.locator('#personaSelect').selectOption(value);
    await page.locator('#personaMemory').fill('QA: répondre avec des exemples courts.');
    await page.locator('#personaMemory').dispatchEvent('change');
    await page.waitForTimeout(900);await page.reload();
    await page.locator('#personaSelect option').last().waitFor({state:'attached'});await page.locator('#personaSelect').selectOption(value);
    await page.waitForFunction(()=>document.querySelector('#personaMemory').value.includes('QA:'));
    await screenshot('personas-desktop');
  });
  await check('Mémoire du compte — sauvegarde des préférences de test',async()=>{
    await visit('/memory/');await page.locator('input[name=enabled]').check();
    await page.locator('textarea[name=content]').fill('Préférences QA Playwright, sans données personnelles.');
    await Promise.all([page.waitForNavigation({waitUntil:'domcontentloaded'}),page.locator('form button').click()]);
    await page.reload({waitUntil:'domcontentloaded'});
    assert.ok((await page.locator('textarea[name=content]').inputValue()).includes('Préférences QA'));
  });
  await page.route('**/chat/api/send/',async route=>{
    const body=route.request().postDataJSON();chatRequests.push(body);
    await new Promise(resolve=>setTimeout(resolve,150));
    const done={type:'done',reply:'Réponse de test Playwright.\n\n- Exemple validé',title:body.content,
      conversation_id:body.conversation_id||null,prompt_tokens:12,completion_tokens:8,remaining_balance:20000,sources:[],tool_events:[]};
    await route.fulfill({status:200,contentType:'text/event-stream',body:'data: '+JSON.stringify(done)+'\n\n'});
  });
  let savedTitle='';
  await check('Conversation temporaire — utilisation directe, mémoire facultative et sauvegarde',async()=>{
    await visit('/');await readyChat();await page.locator('#temporaryChatBtn').click();
    assert.ok(await page.locator('#temporaryChatControls').isVisible());
    assert.ok(await page.locator('#temporaryMemoryControl').isHidden());
    assert.ok(await page.locator('#temporarySaveControl').isHidden());
    assert.equal(await page.locator('#temporaryMemoryPersona').inputValue(),'');
    savedTitle='QA temporaire directe '+Date.now();await page.locator('#composerInput').fill(savedTitle);
    await page.locator('#sendBtn').click();await page.locator('#messages .msg.bot').waitFor();
    assert.equal(chatRequests.at(-1).temporary,true);assert.equal(chatRequests.at(-1).temporary_memory_persona_id,null);
    await page.locator('#temporaryMemoryBtn').click();assert.ok(await page.locator('#temporaryMemoryControl').isVisible());
    const persona=await page.locator('#temporaryMemoryPersona option').last().getAttribute('value');
    await page.locator('#temporaryMemoryPersona').selectOption(persona);
    savedTitle='Mémoire facultative '+Date.now();await page.locator('#composerInput').fill(savedTitle);
    await page.locator('#sendBtn').click();await page.waitForFunction(()=>document.querySelectorAll('#messages .msg.bot').length===2);
    assert.equal(chatRequests.at(-1).temporary_memory_persona_id,Number(persona));
    await page.locator('#temporarySaveBtn').click();assert.ok(await page.locator('#temporarySaveControl').isVisible());
    await page.locator('#temporarySavePersona').selectOption(persona);
    await page.waitForFunction(()=>!document.querySelector('#saveTemporaryConversationBtn').disabled);
    await screenshot('temporary-desktop');
    await page.locator('#saveTemporaryConversationBtn').click();await page.locator('#temporaryChatControls').waitFor({state:'hidden'});
    await page.reload();await readyChat();assert.equal(await page.locator('#messages .msg').count(),4);
    assert.ok((await page.locator('#convoList').innerText()).includes(savedTitle));
    await page.locator('#messages .msg.user button[aria-label="Modifier et régénérer ce message"]').click();
    assert.ok(await page.locator('.message-edit-input').isVisible());await page.locator('.message-edit-cancel').click();
    return {generation:'simulated',persistence:'actual Django'};
  });
  await check('Équipe IA — choix des modèles et stratégie',async()=>{
    await page.locator('#newConvoBtn').click();await page.locator('#teamModeBtn').click();
    const options=page.locator('#teamModelGrid .team-model-option');const count=await options.count();
    if(count<2) return {limitation:'Moins de deux modèles actifs, orchestration non testable.'};
    await options.nth(0).click();await options.nth(1).click();await page.locator('#teamStrategySelect').selectOption('plan_execute_verify');
    if(count>=4){
      await options.nth(2).click();await options.nth(3).click();
      assert.equal(await page.locator('#teamModelSelect').evaluate(el=>el.selectedOptions.length),3);
      await options.nth(2).click();
    }
    await page.locator('#composerInput').fill('QA comparer deux solutions');await page.locator('#sendBtn').click();
    await page.waitForFunction(()=>!document.querySelector('#sendBtn').disabled);
    assert.equal(chatRequests.at(-1).team_mode,true);assert.equal(chatRequests.at(-1).team_model_ids.length,2);
    assert.equal(chatRequests.at(-1).team_strategy,'plan_execute_verify');await page.locator('#teamModeBtn').click();
    return {generation:'simulated',models:2};
  });
  await check('Chat standard — réponse et galeries',async()=>{
    await page.locator('#newConvoBtn').click();await page.locator('#composerInput').fill('QA conversation standard');
    await page.locator('#sendBtn').click();await page.waitForFunction(()=>!document.querySelector('#sendBtn').disabled);
    assert.equal(chatRequests.at(-1).temporary,false);
    assert.ok((await page.locator('#messages').innerText()).includes('Réponse de test Playwright'));
    for(const id of ['openImageGalleryBtn','openVideoGalleryBtn','openAudioGalleryBtn']){
      await page.locator('#'+id).click();assert.ok(await page.locator('#imageGallery').isVisible());
      await page.locator('#closeImageGallery').click();
    }
    return {generation:'simulated'};
  });
  let workspacePath='',workspaceRoot='';
  await check('Bara — création d’un espace de test',async()=>{
    await visit('/bara/');await page.locator('.ws-create-form input[name=title]').fill('Workspace QA Playwright '+Date.now());
    await Promise.all([page.waitForURL(/\/bara\/\d+\//),page.locator('.ws-create-form button[type=submit]').click()]);
    workspacePath=new URL(page.url()).pathname;workspaceRoot='/bara/'+workspacePath.split('/')[2]+'/';await screenshot('bara-desktop');await layout();
  });
  if(workspacePath) {
    await check('Bara — instructions et document de test',async()=>{
      await page.locator('details').filter({has:page.locator('#instructions-input')}).locator('summary').click();await page.locator('#instructions-input').fill('QA: travailler uniquement sur les fichiers de test.');
      const [saved]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===workspaceRoot+'instructions/'),page.locator('#instructions-save').click()]);
      assert.ok(saved.ok());
      const [uploaded]=await Promise.all([page.waitForResponse(r=>new URL(r.url()).pathname===workspaceRoot+'upload/'),
        page.locator('input[type=file]').first().setInputFiles({name:'qa-playwright.txt',mimeType:'text/plain',buffer:Buffer.from('Document de test 2AI. Aucune donnée réelle.\n')})]);
      assert.ok(uploaded.ok(),`Upload: HTTP ${uploaded.status()}`);
      await page.locator('#file-list').getByText('qa-playwright.txt',{exact:true}).waitFor({state:'visible'});
      await page.reload();assert.equal(await page.locator('#instructions-input').inputValue(),'QA: travailler uniquement sur les fichiers de test.');
    });
    await check('Bara — réponse en flux simulée',async()=>{
      const endpoint=await page.locator('#agent-form').getAttribute('action');
      await page.route(origin+endpoint,route=>route.fulfill({contentType:'text/event-stream',body:
        'data: '+JSON.stringify({type:'token',text:'## Réponse Bara de test\n\nDocument de test reçu.'})+'\n\n'
        +'data: '+JSON.stringify({type:'done',remaining_balance:20000})+'\n\n'}));
      await page.locator('#message-content').fill('QA analyser le document de test');await page.locator('#submit-btn').click();
      await page.locator('#messages h2').getByText('Réponse Bara de test').waitFor();
      assert.ok(await page.locator('#submit-btn').isVisible());
      return {generation:'simulated'};
    });
    await check('Bara — modification et historique du document de test',async()=>{
      const editUrl=await page.locator('#file-list a[href$="/edit/"]').first().getAttribute('href');
      await visit(editUrl);await page.locator('#editor-textarea').fill('Document QA Playwright.\nModification de test conservée dans l’historique.');
      await page.locator('#save-btn').click();await page.waitForFunction(()=>document.querySelector('#save-status').textContent.includes('Enregistré'));
      await page.reload({waitUntil:'domcontentloaded'});assert.ok((await page.locator('#editor-textarea').inputValue()).includes('Modification de test'));
      await visit(editUrl.replace('/edit/','/history/'));await layout();await visit(workspacePath);
    });
  }
  const paths=['/','/tarifs/','/recharge/','/customize/','/memory/','/bara/','/api/','/api-keys/','/nous/','/juridique/'];
  if(workspacePath) paths.push(workspacePath,...['tasks/','journal/','connections/','sharing/','schedules/'].map(suffix=>workspaceRoot+suffix));
  for(const width of [1440,390]) {
    await page.setViewportSize({width,height:width<600?844:1000});
    for(const url of paths) await check(`Navigation ${width} — ${url}`,async()=>{
      await visit(url);if(url==='/')await readyChat();return layout();
    });
    await check(`Recharge ${width} — devises, montants et validations sans paiement`,async()=>{
      await visit('/recharge/');await page.locator('[data-payment-currency=USD]').click();assert.ok(await page.locator('#stripePayment').isVisible());
      await page.locator('[data-set-amount="stripe-amount"][data-amount="10"]').click();
      assert.equal(await page.locator('#stripe-amount').inputValue(),'10');
      const estimate=await page.locator('#stripeCreditsEstimate').innerText();assert.ok(estimate.includes('jetons'));
      await page.locator('#stripe-amount').fill('1');assert.equal(await page.locator('#stripe-amount').evaluate(el=>el.checkValidity()),false);
      await page.locator('[data-payment-currency=XOF]').click();assert.ok(await page.locator('#paydunyaPayment').isVisible());
      await screenshot(`recharge-${width}`);return {checkoutRequests:report.blocked.length};
    });
    await check(`Chat ${width} — menus et conversation temporaire`,async()=>{
      await visit('/');await readyChat();if(width<600){await page.locator('#openMobileSidebar').click();assert.ok(await page.locator('.sidebar').isVisible());await page.locator('#closeMobileSidebar').click();}
      await page.locator('#temporaryChatBtn').click();assert.ok(await page.locator('#temporaryChatControls').isVisible());assert.ok(await page.locator('#temporaryMemoryControl').isHidden());
      await screenshot(`temporary-${width}`);await layout();await page.locator('#temporaryChatBtn').click();assert.ok(await page.locator('#temporaryChatControls').isHidden());
    });
    await check(`Tarifs ${width} — filtre des modèles`,async()=>{
      await visit('/tarifs/');await page.locator('#tariffsSearch').fill('qa-no-result');
      assert.ok(await page.locator('#tariffsNoResults').isVisible());await page.locator('#tariffsSearch').fill('');
      assert.ok(await page.locator('#tariffsNoResults').isHidden());
    });
  }
  await check('Connexion et inscription — affichage mobile et champs requis',async()=>{
    await visit('/auth/');await layout();await screenshot('auth-mobile');
    await page.locator('#tabSignup').click();assert.ok(await page.locator('#panelSignup').isVisible());
    await page.locator('#id_signup_email').fill('email-invalide');
    assert.equal(await page.locator('#id_signup_email').evaluate(el=>el.checkValidity()),false);
    await page.locator('#linkToLogin').click();assert.ok(await page.locator('#panelLogin').isVisible());
    await page.locator('#waToggle').click();assert.ok(await page.locator('#waPanel').isVisible());
    await page.locator('#waSendOtp').click();assert.ok((await page.locator('.otp-status').first().innerText()).includes('Renseigne'));
  });
  report.finished=new Date().toISOString();report.blockedRequestCount=report.blocked.length;
  report.forbiddenActionsExecuted=0;
  report.summary={passed:report.cases.filter(x=>x.status==='passed').length,
    failed:report.cases.filter(x=>x.status==='failed').length,javascriptErrors:report.javascript.length};
  fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));
  console.log(JSON.stringify({passed:report.cases.filter(x=>x.status==='passed').length,failed:report.cases.filter(x=>x.status==='failed').length,javascript:report.javascript,network:report.network,report:path.join(output,'report.json')}));
  if(report.summary.failed || report.summary.javascriptErrors) process.exitCode=1;
})().catch(error=>{report.fatal=error.message;fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));console.error(error.message);process.exitCode=1;}).finally(async()=>{await browser?.close();});
