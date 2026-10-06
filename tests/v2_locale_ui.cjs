// V2-only UI acceptance: actual three-tab shell, unified memories/review,
// page Settings, three onboarding steps and tray Flyout. Synthetic API only.
const { chromium }=require('C:/Users/Ryan D/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const {spawn}=require('node:child_process');
const fs=require('node:fs'),path=require('node:path'),net=require('node:net');
const repo=path.resolve(__dirname,'..'),out=path.join(__dirname,'results/locale-v2');
fs.mkdirSync(out,{recursive:true});
async function freePort(){const s=net.createServer();await new Promise(r=>s.listen(0,'127.0.0.1',r));const p=s.address().port;await new Promise(r=>s.close(r));return p;}
(async()=>{
 const port=await freePort();
 const server=spawn(process.execPath,[path.join(repo,'apps/web/node_modules/vite/bin/vite.js'),'--host','127.0.0.1','--port',String(port),'--strictPort'],{cwd:path.join(repo,'apps/web'),windowsHide:true,stdio:'ignore'});
 const result={version:'V2',platform:process.platform,port,mode:'isolated frontend synthetic API mocks; no real databases or clients',checks:[],errors:[]};
 let browser;
 try{
  for(let i=0;i<100;i++){try{if((await fetch(`http://127.0.0.1:${port}`)).ok)break;}catch{}await new Promise(r=>setTimeout(r,100));}
  browser=await chromium.launch({headless:true,channel:'msedge'});
  const context=await browser.newContext({viewport:{width:1440,height:900}}),page=await context.newPage();
  page.on('pageerror',e=>result.errors.push(String(e)));
  await page.route('**/api/**',r=>r.fulfill({status:503,contentType:'application/json',body:JSON.stringify({error:{code:'UNAVAILABLE',message:'Synthetic offline fixture'}})}));
  await page.goto(`http://127.0.0.1:${port}`);
  await page.locator('h1').waitFor();
  const text=await page.locator('body').innerText();
  result.checks.push({name:'V2 fresh session defaults English',status:/Welcome back to OMNA/.test(text)?'PASS':'FAIL',visible:text});
  await page.screenshot({path:path.join(out,process.env.LOCALE_BASELINE?'before-default.png':'default.png'),fullPage:true});
  if(!process.env.LOCALE_BASELINE && result.checks[0].status==='PASS'){
   const {memory,proposal,agent,fixture,organizePrompt}=require('./v2_locale_fixtures.cjs');
   let offline=false;result.unhandledFixtures=[];
   await page.unroute('**/api/**');
   const route=async r=>{const url=new URL(r.request().url()),data=fixture(url);if(data===undefined)result.unhandledFixtures.push(url.pathname);return r.fulfill({status:offline?503:data===undefined?404:200,contentType:'application/json',body:JSON.stringify(offline?{error:{code:'UNAVAILABLE',message:'原始合成诊断：服务不可用。'}}:data===undefined?{error:{code:'NOT_FOUND',message:'Missing synthetic fixture'}}:data)});};
   await context.route('**/api/**',route);
   await page.evaluate(()=>{sessionStorage.setItem('zhiwo-owner-credential','synthetic-v2');localStorage.setItem('omna-onboarding','done');});
   const check=(name,ok,detail)=>result.checks.push({name,status:ok?'PASS':'FAIL',detail});
   const userData=[memory.content,memory.scope,proposal.payload.content,proposal.evidence.text,agent.name,'原始合成诊断：服务不可用。','简体中文'];
   const uiText=async target=>{let text=await target.innerText();for(const value of userData)text=text.replaceAll(value,'');return text;};
   const scan=async(name,target=page.locator('body'))=>check(name,!/[\u3400-\u9fff]/.test(await uiText(target)),await target.innerText());
   const capture=async name=>{for(const [width,height]of[[1440,900],[1024,768]]){await page.setViewportSize({width,height});await page.screenshot({path:path.join(out,`${name}-${width}.png`),fullPage:true});check(`${name}: no horizontal overflow ${width}`,await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth));}await page.setViewportSize({width:1440,height:900});};
   await page.goto(`http://127.0.0.1:${port}/#/settings`);await page.reload();
   await page.getByLabel('Language',{exact:true}).waitFor();await capture('settings-en');
   await scan('V2 English Settings appearance',page.locator('main'));
   for(let tab=1;tab<await page.getByRole('tab').count();tab++){await page.getByRole('tab').nth(tab).click();await scan(`V2 Settings subsection ${tab} English`,page.locator('main'));}await page.getByRole('tab').first().click();
   const englishDate=await page.evaluate(async()=> (await import('/src/format.ts')).dateLabel('2025-01-02T13:04:00Z'));
   await page.getByLabel('Language',{exact:true}).selectOption('zh-CN');
   check('Settings changes immediately to Chinese',await page.getByLabel('语言',{exact:true}).count()===1&&await page.locator('html').getAttribute('lang')==='zh-CN');
   await page.reload();await page.getByLabel('语言',{exact:true}).waitFor();check('Chinese survives refresh',await page.locator('html').getAttribute('lang')==='zh-CN');
   const chineseDate=await page.evaluate(async()=> (await import('/src/format.ts')).dateLabel('2025-01-02T13:04:00Z'));
   check('Dates change with locale',englishDate!==chineseDate,{en:englishDate,zh:chineseDate});
   await page.getByLabel('语言',{exact:true}).selectOption('en');
   await page.goto(`http://127.0.0.1:${port}/#/`);
   await page.locator('.mem-detail').waitFor();await page.getByText(memory.content,{exact:true}).first().waitFor();
   check('V2 has exactly three primary tabs',await page.locator('.tabs .tab').count()===3);
   await scan('V2 English memory list and inline detail');await capture('memories-en');
   await page.locator('.mem-detail-actions button').first().click();const editor=page.locator('.mem-detail textarea');await editor.waitFor();
   check('Chinese memory not translated in editor',await editor.inputValue()===memory.content);
   check('Chinese context remains raw in editor',await page.locator('.detail-editor input').inputValue()===memory.scope);
   const draft='未保存的中文草稿 {0} {{保留}}';await editor.fill(draft);
   await page.evaluate(async()=> (await import('/src/i18n.ts')).setLocale('zh-CN'));
   check('Chinese switch retains editing draft',await editor.inputValue()===draft);
   await page.evaluate(async()=> (await import('/src/i18n.ts')).setLocale('en'));
   check('English switch retains editing draft',await editor.inputValue()===draft);
   check('Chinese brace interpolation remains raw',(await page.evaluate(async()=> (await import('/src/i18n.ts')).t('筛选，{0}',['中文 {0} {{保留}}']))).includes('中文 {0} {{保留}}'));
   await editor.fill(memory.content);await capture('detail-en');
   await page.goto(`http://127.0.0.1:${port}/#/review`);await page.getByText(proposal.payload.content,{exact:true}).first().waitFor();await scan('V2 unified pending memory view English');await page.screenshot({path:path.join(out,'review-en.png'),fullPage:true});
   await page.goto(`http://127.0.0.1:${port}/#/agents`);await page.locator('.ci').waitFor();await scan('V2 connected Agent and ConnectImport offer English');await page.screenshot({path:path.join(out,'agents-connectimport-en.png'),fullPage:true});
   await page.locator('.ci-more').click();await scan('V2 ConnectImport menu English');await page.keyboard.press('Escape');
   await page.locator('.ci-actions .button.primary').click();await page.locator('.ci-prompt').waitFor();await scan('V2 ConnectImport copied product instruction English',page.locator('.ci-prompt'));
   await page.evaluate(()=>Object.defineProperty(navigator,'clipboard',{configurable:true,value:{writeText:async value=>{window.__copied=value;}}}));
   await page.locator('.ci-prompt button').click();check('ConnectImport clipboard equals English displayed instruction',await page.evaluate(()=>window.__copied)===await page.locator('.ci-prompt span').first().innerText());
   check('ConnectImport retains raw server instruction in stored session',await page.evaluate(()=>JSON.parse(localStorage.getItem('omna-import-card:locale-agent')).prompt)===organizePrompt);
   await page.evaluate(async()=> (await import('/src/i18n.ts')).setLocale('zh-CN'));check('Known product instruction returns to Chinese',await page.locator('.ci-prompt span').first().innerText()===organizePrompt);
   await page.evaluate(async()=> (await import('/src/i18n.ts')).setLocale('en'));check('Unknown custom prompt remains Chinese verbatim',await page.evaluate(async()=> (await import('/src/i18n.ts')).organizePrompt('用户自己写的中文指令 {0}'))==='用户自己写的中文指令 {0}');
   await page.goto(`http://127.0.0.1:${port}/#/onboarding`);await page.locator('.ob-title').waitFor();
   for(let step=1;step<=3;step++){if(step===2){await page.locator('.ob-organizer-row button').click();await page.locator('.ob-prompt').waitFor();await scan('V2 onboarding raw server instruction English',page.locator('.ob-prompt'));await page.locator('.ob-prompt button').click();check('Onboarding clipboard equals English displayed instruction',await page.evaluate(()=>window.__copied)===await page.locator('.ob-prompt span').first().innerText());}await scan(`V2 onboarding screen ${step} English`);await page.screenshot({path:path.join(out,`onboarding-${step}-en.png`),fullPage:true});if(step<3)await page.locator('.ob-foot .button.ghost').click();}
   await context.addInitScript(()=>sessionStorage.setItem('zhiwo-owner-credential','synthetic-v2'));
   const flyout=await context.newPage();await flyout.goto(`http://127.0.0.1:${port}/#/flyout`);await flyout.locator('.flyout').waitFor();await flyout.getByText(proposal.payload.content,{exact:true}).first().waitFor();await scan('V2 flyout English',flyout.locator('body'));await flyout.screenshot({path:path.join(out,'flyout-en.png'),fullPage:true});await flyout.close();
   await page.locator('.ob-foot .button.primary').click();
   offline=true;await page.reload();await page.locator('.global-notice').waitFor();
   await page.goto(`http://127.0.0.1:${port}/#/settings`);await page.getByLabel('Language',{exact:true}).waitFor();await page.getByLabel('Language',{exact:true}).selectOption('zh-CN');
   check('Existing offline notice immediately Chinese',/本地服务未运行/.test(await page.locator('.global-notice').innerText()));
   await page.getByLabel('语言',{exact:true}).selectOption('en');await scan('Existing offline notice immediately English',page.locator('.global-notice'));
   check('No missing synthetic fixture',result.unhandledFixtures.length===0,result.unhandledFixtures);check('No browser runtime errors',result.errors.length===0,result.errors);
   result.notRun=['Real native cross-window main/flyout locale sync and packaged installer (separate native QA)','Native tray/notifications UI (separate native QA)','Model requests or real client configuration'];
  }else result.notRun=['Final V2 locale workflow acceptance awaits implementation-ready notice'];
  result.status=result.checks.every(c=>c.status==='PASS')?'PASS':'FAIL';
 }catch(e){result.status='FAIL';result.error=String(e);}
 finally{if(browser)await browser.close();server.kill();fs.writeFileSync(path.join(out,process.env.LOCALE_BASELINE?'before.json':'after.json'),JSON.stringify(result,null,2));}
 console.log(JSON.stringify(result,null,2));process.exitCode=result.status==='PASS'?0:1;
})();
