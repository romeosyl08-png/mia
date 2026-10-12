// Real Django workflows; only synthetic media and qa_playwright accounts.
// Paid generations are never queued. Director output is simulated in the browser.
const {chromium} = require('playwright');
const fs = require('node:fs');
const path = require('node:path');
const assert = require('node:assert/strict');
const {execFile} = require('node:child_process');
const {promisify} = require('node:util');
const execute = promisify(execFile);
const origin = 'http://127.0.0.1:8000';
const output = '/tmp/2ai-studio-audit';
fs.mkdirSync(output,{recursive:true});
const credentials = JSON.parse(fs.readFileSync('/tmp/2ai-playwright-audit/test-account.json','utf8'));
assert.ok(credentials.username.startsWith('qa_playwright_'),'Only an existing test account is allowed');
const report = {cases:[],javascript:[],network:[],screenshots:[],account:credentials.username,
  scope:'Real uploads, scenes, versions, private media, masks and free FFmpeg jobs. No paid generation or payment. Director response simulated.'};
let browser,context,page,base,step='';
const wait = async fn => {
  for(let i=0;i<60;i++) {const value=await fn();if(value)return value;await new Promise(resolve=>setTimeout(resolve,500));}
  throw new Error('Timed out waiting for the studio task');
};
async function state(){try{const response=await context.request.get(origin+base+'state/');assert.equal(response.status(),200);return await response.json();}catch(error){throw new Error(error.message.split('Call log:')[0].trim());}}
async function shot(name){const file=path.join(output,name+'.png');await page.screenshot({path:file,fullPage:true});report.screenshots.push(file);}
async function check(name,fn){step=name;try{const detail=await fn();report.cases.push({name,status:'passed',detail});console.log('PASS',name);}catch(error){const message=error.message.split('Call log:')[0].trim();report.cases.push({name,status:'failed',error:message});console.log('FAIL',name,message.slice(0,350));await shot('failed-'+report.cases.length);throw new Error('Audit interrompu : '+message);}finally{fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));}}
async function processJob(id){
  const snapshot=await state(),job=snapshot.jobs.find(item=>item.id===id);
  assert.ok(job && job.reserved_credits===0,'QA worker only runs zero-credit jobs owned by this test project');
  await execute('/home/profhorse/freemaker/venv/bin/python',['manage.py','studio_worker','--job-id',String(id)],{cwd:path.resolve(__dirname,'../..'),timeout:90000,maxBuffer:1024*1024});
  const value=await wait(async()=>{const s=await state();const j=s.jobs.find(item=>item.id===id);return j && !['queued','running'].includes(j.status) && s;});
  const completed=value.jobs.find(item=>item.id===id);assert.equal(completed.status,'completed',completed.error);
  await page.reload({waitUntil:'domcontentloaded'});
  await page.waitForFunction(expected=>document.querySelector('#studio-version')?.value===String(expected),value.version_id);
  return completed;
}
async function launchFree(){const before=await state();await page.locator('#studio-launch button').click();const job=await wait(async()=>{const s=await state();return s.jobs.find(item=>!before.jobs.some(old=>old.id===item.id));});assert.equal(job.reserved_credits,0);return job;}

(async()=>{
  await execute('/home/profhorse/freemaker/venv/bin/python',['-c',
    "from PIL import Image; import wave,math,struct; Image.new('RGB',(512,384),(25,95,175)).save('/tmp/2ai-studio-audit/source.png'); w=wave.open('/tmp/2ai-studio-audit/voice.wav','wb'); w.setnchannels(1); w.setsampwidth(2); w.setframerate(8000); w.writeframes(b''.join(struct.pack('<h',int(5000*math.sin(i*2*math.pi*440/8000))) for i in range(16000))); w.close()"]);
  browser=await chromium.launch({executablePath:'/opt/google/chrome/chrome',headless:true,args:['--no-sandbox']});
  context=await browser.newContext({viewport:{width:1440,height:1000}});
  await context.route('**/*',async route=>{
    const url=new URL(route.request().url());
    if(url.origin===origin && (/\/delete\/$/.test(url.pathname) || url.pathname.includes('checkout') || url.pathname==='/recharge/paydunya/'))throw new Error('Excluded action');
    if(url.origin===origin && /\/studio\/\d+\/director\/$/.test(url.pathname)){
      let snapshot;
      try{snapshot=await state();}catch(error){return route.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:error.message})});}
      return route.fulfill({status:200,contentType:'application/json',body:JSON.stringify({message:'Plan de test simulé : régler la durée du premier plan.',charged_credits:0,balance:snapshot.balance,expected_version:snapshot.version_id,
        operations:[{operation:'update_track',track_id:snapshot.scene.visuals[0].id,changes:{duration:.75}}]})});
    }
    if(url.origin===origin && /\/studio\/\d+\/jobs\/$/.test(url.pathname) && route.request().method()==='POST'){
      const payload=route.request().postDataJSON();
      assert.ok(payload.operations.every(row=>['render','recolor_image','update_track'].includes(row.operation)),'Paid operations cannot be queued by QA');
    }
    return route.continue();
  });
  page=await context.newPage();page.setDefaultTimeout(20000);
  page.on('pageerror',error=>report.javascript.push({step,error:error.message}));
  page.on('requestfailed',request=>{if(request.failure()?.errorText!=='net::ERR_ABORTED')report.network.push({step,path:new URL(request.url()).pathname,error:request.failure()?.errorText});});
  page.on('response',response=>{if(response.status()>=400)report.network.push({step,path:new URL(response.url()).pathname,status:response.status()});});
  await page.goto(origin+'/auth/?next=/');await page.locator('#id_login_username').fill(credentials.username);await page.locator('#id_login_password').fill(credentials.password);
  await Promise.all([page.waitForURL(url=>url.pathname==='/'),page.locator('#panelLogin button[type=submit]').click()]);
  await check('Navigation chat vers studio',async()=>{await page.getByRole('link',{name:'Studio multimédia'}).click();await page.waitForURL('**/studio/');assert.ok(await page.getByRole('heading',{name:'Nouveau projet'}).isVisible());});
  await check('Création du projet avec un persona de test',async()=>{
    await page.locator('[name=title]').fill('Studio QA '+Date.now());const persona=await page.locator('[name=persona] option').nth(1).getAttribute('value');await page.locator('[name=persona]').selectOption(persona);
    await Promise.all([page.waitForURL(/\/studio\/\d+\/$/),page.getByRole('button',{name:'Créer le projet'}).click()]);base=new URL(page.url()).pathname;report.project=base;
    await page.locator('#studio-balance').waitFor();assert.equal((await state()).versions.length,1);
  });
  await check('Import image privée et ajout de deux plans',async()=>{
    await page.locator('#studio-upload input[type=file]').setInputFiles(path.join(output,'source.png'));await page.locator('#studio-upload button').click();
    const card=page.locator('.studio-asset').filter({has:page.getByText('source.png',{exact:true})});await card.waitFor();
    await card.getByRole('button',{name:'Ajouter un plan'}).click();await card.getByRole('button',{name:'Ajouter un plan'}).click();
    await page.locator('#studio-visuals input[type=number]').nth(0).fill('0.5');await page.locator('#studio-visuals input[type=number]').nth(1).fill('0.75');
    await page.locator('#studio-brief').fill('Projet synthétique : ambiance bleue.');await page.locator('#studio-save').click();
    await wait(async()=>{const s=await state();return s.scene.visuals.length===2&&s.scene.visuals[1].duration===.75;});
  });
  await check('Piste voix, volume, décalage et durée audio',async()=>{
    await page.locator('#studio-upload input[type=file]').setInputFiles(path.join(output,'voice.wav'));await page.locator('#studio-upload button').click();
    const card=page.locator('.studio-asset').filter({has:page.getByText('voice.wav',{exact:true})});await card.waitFor();await card.getByRole('button',{name:'Ajouter une piste'}).click();
    await page.locator('#studio-audio select').selectOption('voice');await page.locator('#studio-audio input[type=number]').nth(0).fill('0.4');await page.locator('#studio-audio input[type=number]').nth(1).fill('0.1');await page.locator('#studio-duration').fill('2');
    await page.locator('#studio-save').click();await wait(async()=>{const s=await state();return s.scene.audio[0]?.volume===.4&&s.scene.settings.duration===2;});
  });
  await check('Verrouillage et déplacement des plans',async()=>{
    await page.locator('#studio-visuals .studio-track').first().getByRole('button',{name:'Verrouiller',exact:true}).click();assert.ok(await page.locator('#studio-visuals input[type=number]').first().isDisabled());
    await page.locator('#studio-visuals .studio-track').first().getByRole('button',{name:'Déverrouiller'}).click();
    await page.locator('#studio-visuals .studio-track').first().getByRole('button',{name:'Déplacer après'}).click();await page.locator('#studio-save').click();
    await wait(async()=>{const s=await state();return s.scene.visuals[0].duration===.75;});
  });
  let beforeMask,maskJob;
  await check('Peinture du masque et recoloration locale réelle',async()=>{
    beforeMask=(await state()).version_id;
    await page.locator('.studio-asset').filter({has:page.getByText('source.png',{exact:true})}).getByRole('button',{name:'Peindre un masque'}).click();
    const box=await page.locator('#studio-mask-canvas').boundingBox();assert.ok(box);
    await page.mouse.move(box.x+box.width*.15,box.y+box.height*.2);await page.mouse.down();await page.mouse.move(box.x+box.width*.35,box.y+box.height*.65,{steps:20});await page.mouse.up();
    await shot('desktop-mask');await page.locator('#studio-recolor').click();await page.locator('#studio-plan').waitFor({state:'visible'});assert.match(await page.locator('#studio-estimate').textContent(),/0 jetons/);
    const job=await launchFree();maskJob=await processJob(job.id);assert.ok(maskJob.result_version_id);
    const s=await state(),original=s.assets.find(a=>a.name==='source.png'),changed=s.assets.find(a=>a.id===maskJob.output_ids[0]);
    const pixels=await page.evaluate(async({first,second})=>{async function sample(url){const i=new Image();i.src=url;await i.decode();const c=document.createElement('canvas');c.width=i.width;c.height=i.height;const x=c.getContext('2d');x.drawImage(i,0,0);return [...x.getImageData(450,200,1,1).data];}return [await sample(first),await sample(second)];},{first:original.url,second:changed.url});assert.deepEqual(pixels[0],pixels[1]);
  });
  await check('Restauration, conservation puis reprise du résultat',async()=>{
    await page.locator('#studio-version').selectOption(String(beforeMask));await page.locator('#studio-restore').click();
    await wait(async()=>{const s=await state();return s.version_id!==maskJob.result_version_id&&s.scene.visuals[0].asset_id!==maskJob.output_ids[0];});
    await page.locator('#studio-version').selectOption(String(maskJob.result_version_id));await page.locator('#studio-restore').click();
    await wait(async()=>{const s=await state();return s.scene.visuals[0].asset_id===maskJob.output_ids[0];});
  });
  await check('Montage vidéo réel, bande-son, aperçu et téléchargement',async()=>{
    await page.locator('#studio-render-video').click();await page.locator('#studio-plan').waitFor({state:'visible'});const job=await launchFree();await processJob(job.id);
    const video=page.locator('#studio-exports video');await video.waitFor();await wait(async()=>video.evaluate(v=>Number.isFinite(v.duration)&&v.duration>0));
    const promise=page.waitForEvent('download');await page.locator('#studio-exports a').last().click();const download=await promise;const file=path.join(output,'montage.mp4');await download.saveAs(file);
    const probe=JSON.parse((await execute('ffprobe',['-v','error','-show_format','-show_streams','-of','json',file])).stdout);
    assert.ok(probe.streams.some(s=>s.codec_type==='audio'));assert.ok(Math.abs(Number(probe.format.duration)-1.25)<.1);
  });
  await check('Mix audio réel et lecture privée',async()=>{
    await page.locator('#studio-render-audio').click();await page.locator('#studio-plan').waitFor({state:'visible'});const job=await launchFree();await processJob(job.id);
    const audio=page.locator('#studio-exports audio');await audio.waitFor();await wait(async()=>audio.evaluate(a=>Number.isFinite(a.duration)&&a.duration>=1.9));
  });
  await check('Directeur simulé, plan contrôlé et application locale',async()=>{
    await page.locator('#studio-request').fill('Règle la durée du premier plan à 0,75 seconde.');await page.locator('#studio-director button').click();await page.locator('#studio-plan').waitFor({state:'visible'});
    assert.match(await page.locator('#studio-plan-message').textContent(),/simulé/);const job=await launchFree();await processJob(job.id);
  });
  await check('Options image et vidéo, estimation sans génération payante',async()=>{
    await page.locator('#studio-kind').selectOption('image');await page.locator('#studio-prompt').fill('Image de test, ne pas générer');await page.locator('#studio-generate button').click();await page.locator('#studio-plan').waitFor({state:'visible'});
    assert.ok(!/Estimation : 0/.test(await page.locator('#studio-estimate').textContent()));
    await page.locator('#studio-kind').selectOption('video');await page.locator('#studio-video-duration').selectOption('4');await page.locator('#studio-video-aspect').selectOption('9:16');await page.locator('#studio-generate button').click();
    await wait(async()=>/generate_audio/.test(await page.locator('#studio-plan-steps').textContent()));assert.match(await page.locator('#studio-plan-steps').textContent(),/9:16/);
    await page.locator('#studio-kind').selectOption('audio');assert.ok(await page.locator('#studio-audio-style').isVisible());
  });
  await check('Annulation d’une tâche locale en attente',async()=>{
    await page.locator('#studio-render-audio').click();await page.locator('#studio-plan').waitFor({state:'visible'});const job=await launchFree();
    await page.locator('#studio-jobs .studio-job').filter({has:page.getByText(`#${job.id} · En attente`,{exact:true})}).getByRole('button',{name:'Annuler'}).click();
    await wait(async()=>{const s=await state();return s.jobs.find(item=>item.id===job.id)?.status==='cancelled';});
  });
  await check('Ordinateur, tablette et mobile sans débordement',async()=>{
    for(const width of [1440,768,390]){await page.setViewportSize({width,height:1000});await page.waitForTimeout(200);const layout=await page.evaluate(()=>({width:innerWidth,scroll:document.documentElement.scrollWidth}));assert.ok(layout.scroll<=layout.width+1,JSON.stringify(layout));await shot('studio-'+width);}
    await page.locator('#studio-director-budget').fill('1');assert.equal(await page.locator('#studio-director-budget').inputValue(),'1');
  });
  assert.equal(report.javascript.length,0,JSON.stringify(report.javascript));
  assert.equal(report.network.length,0,JSON.stringify(report.network));
})().catch(error=>{report.fatal=error.message.split('Call log:')[0].trim();console.error(report.fatal);process.exitCode=1;}).finally(async()=>{
  report.finished=new Date().toISOString();fs.writeFileSync(path.join(output,'report.json'),JSON.stringify(report,null,2));if(browser)await browser.close();
  if(report.cases.some(item=>item.status==='failed'))process.exitCode=1;
});
