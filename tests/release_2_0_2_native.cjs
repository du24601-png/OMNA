// Real V2 Windows package, bundled backend and kernel; isolated synthetic data.
const { chromium } = require('C:/Users/Ryan D/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const out=path.join(__dirname,'results/english-release-2.0.2');
const run=path.join(root,'.cache/english-release-2.0.2',String(Date.now()));
const userData=path.join(run,'user-data'),home=path.join(run,'home');
const appRoot=path.join(root,'apps/desktop/dist/release-2.0.2/win-unpacked');
const exe=path.join(appRoot,'OMNA.exe'),origin='http://127.0.0.1:8765';
const preference='I prefer concise replies with clear next steps.';
const wait=ms=>new Promise(r=>setTimeout(r,ms));
const result={platform:process.platform,mode:'Actual V2 packaged Electron executable; real bundled Python/Mnemosyne; ten synthetic English memories; no API mocks',capture:'CDP screenshot of actual Electron webContents at native default window size, not an OS desktop screenshot',checks:[],screenshots:[],not_run:['NSIS installation/upgrade/uninstall','Clean-machine acceptance','OS tray menu click and notification delivery','Real third-party MCP client/model']};
const check=(name,ok,detail)=>{result.checks.push({name,status:ok?'PASS':'FAIL',...(detail!==undefined?{detail}: {})});if(!ok)throw Error(name);};
const hash=p=>crypto.createHash('sha256').update(fs.readFileSync(p)).digest('hex');
async function freePort(port=0){const s=net.createServer();await new Promise((r,j)=>{s.once('error',j);s.listen(port,'127.0.0.1',r)});const p=s.address().port;await new Promise(r=>s.close(r));return p;}
async function api(route,owner,body){const r=await fetch(origin+route,{method:body?'POST':'GET',headers:{Authorization:`Bearer ${owner}`,...body?{'Content-Type':'application/json','Idempotency-Key':crypto.randomUUID()}:{}},body:body?JSON.stringify(body):undefined});const data=await r.json();if(!r.ok)throw Error(`API ${route}: ${r.status} ${JSON.stringify(data)}`);return data;}
const env={};
for(const key of ['SystemRoot','WINDIR','SystemDrive','TEMP','TMP','USERNAME','USERDOMAIN','COMPUTERNAME','PROGRAMDATA','PROGRAMFILES','PROGRAMFILES(X86)','PROGRAMW6432','COMMONPROGRAMFILES','COMMONPROGRAMFILES(X86)','PROCESSOR_ARCHITECTURE','NUMBER_OF_PROCESSORS','PATHEXT','ComSpec','OS'])if(process.env[key])env[key]=process.env[key];
Object.assign(env,{PATH:path.join(process.env.SystemRoot,'System32'),USERPROFILE:home,HOME:home,HOMEDRIVE:path.parse(home).root.slice(0,2),HOMEPATH:home.slice(2),APPDATA:path.join(home,'AppData/Roaming'),LOCALAPPDATA:path.join(home,'AppData/Local')});
let browser,child,main,flyout;
async function launch(){
 const port=await freePort();let launchError;
 child=spawn(exe,[`--omna-user-data=${userData}`,`--remote-debugging-port=${port}`],{env,windowsHide:true,stdio:'ignore'});
 child.on('error',err=>launchError=err);result.last_pid=child.pid;
 for(let i=0;i<360;i++){
  if(launchError)throw launchError;
  if(child.exitCode!==null)throw Error(`Electron exited before ready: ${child.exitCode}`);
  try{if(!browser)browser=await chromium.connectOverCDP(`http://127.0.0.1:${port}`,{timeout:1500});const pages=browser.contexts()[0].pages();main=pages.find(p=>p.url().startsWith(origin)&&!p.url().includes('#/flyout'));flyout=pages.find(p=>p.url().startsWith(origin)&&p.url().includes('#/flyout'));if(main&&flyout)break;}catch{}
  await wait(500);
 }
 if(!main||!flyout)throw Error('Main window and real flyout were not ready');
 await main.waitForLoadState('domcontentloaded');await flyout.waitForLoadState('domcontentloaded');
}
async function stop(){
 if(browser){await browser.close();browser=undefined;}
 if(!child)return;
 if(child.exitCode===null){const quit=spawn(exe,[`--omna-user-data=${userData}`,'--quit'],{env,windowsHide:true,stdio:'ignore'});quit.unref();}
 // Wait for the original Electron process to exit, not just for its service port.
 for(let i=0;i<80&&child.exitCode===null;i++)await wait(250);
 const exited=child.exitCode!==null;child.unref();child=undefined;main=undefined;flyout=undefined;
 if(!exited)throw Error('Isolated Electron did not exit');
 if(await freePort(8765)!==8765)throw Error('Isolated backend port was not released');
}
async function capture(page,name){const file=path.join(out,name);await page.screenshot({path:file});const png=fs.readFileSync(file);result.screenshots.push({path:file,width:png.readUInt32BE(16),height:png.readUInt32BE(20),sha256:hash(file)});}
(async()=>{
 fs.mkdirSync(out,{recursive:true});for(const folder of [userData,env.APPDATA,env.LOCALAPPDATA])fs.mkdirSync(folder,{recursive:true});
 fs.writeFileSync(path.join(userData,'desktop.json'),JSON.stringify({notifications:false,shortcut:'CommandOrControl+Alt+Shift+F12'}));
 result.started_at=new Date().toISOString();result.source_worktree=root;result.branch=execFileSync('git',['branch','--show-current'],{cwd:root,encoding:'utf8'}).trim();result.head=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();result.package_version=JSON.parse(fs.readFileSync(path.join(root,'apps/desktop/package.json'),'utf8')).version;result.exe=exe;result.user_data=userData;result.client_home=home;
 check('Actual source is the V2 English release branch and 2.0.2 package',result.package_version==='2.0.2' && execFileSync('git',['merge-base','--is-ancestor','7dbf4a10105420200dc192c91d53e4c327aab164','HEAD'],{cwd:root}).length===0);
 check('8765 initially free; no existing service stopped',await freePort(8765)===8765);
 await launch();
 check('Main window and native V2 flyout both loaded actual service UI',!!main&&!!flyout);
 check('Native preload default English in both windows',await main.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en')&&await flyout.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en'));
 const owner=fs.readFileSync(path.join(userData,'owner.credential'),'utf8').trim();
 const health=await api('/api/v1/health',owner);result.health=health;
 check('Real bundled embeddings ready; test mode off; extractor unconfigured',health.embeddings_loaded===true&&health.test_mode===false&&health.extractor_configured===false);
 const memories=[
  ['identity','I am a product manager working on useful AI tools.','Work'],
  ['identity','I collaborate with designers, engineers, and researchers.','Work'],
  ['goal','Learn from a focused beta before expanding the roadmap.','This quarter'],
  ['goal','Keep two hours each morning for focused work.','Daily routine'],
  ['project','OMNA is my shared memory library for the AI tools I use.','OMNA'],
  ['project','Use real product screenshots on the next launch page.','Launch'],
  ['preference','Write notes in Markdown with clear action items.','Team notes'],
  ['preference','Include sources and label assumptions in research.','Research'],
  ['preference','Suggest the smallest useful next step first.','Product decisions'],
  ['preference',preference,'Everyday communication'],
 ];
 for(const [category,content,scope]of memories){const saved=await api('/api/v1/memories',owner,{content,category,scope,kind:'fact',share_enabled:true,source_refs:[]});check(`Real backend saved synthetic memory ${result.checks.length}`,saved.status==='accepted');}
 check('Real Mnemosyne-backed API lists exactly ten memories',(await api('/api/v1/memories',owner)).items.length===10);
 await main.evaluate(()=>localStorage.setItem('omna-onboarding','done'));
 await main.goto(origin+'/#/');
  result.pre_demo_reload_view=await main.evaluate(()=>({onboarding:!!document.querySelector('.ob'),hash:location.hash}));
  // Hash navigation does not reset the already-open first-run React onboarding state.
  await main.reload();
 await main.waitForFunction(()=>document.querySelectorAll('button.mem-row').length===10);
 const nav=await main.locator('.tabs .tab').allTextContents();
 check('Actual V2 has exactly three text navigation tabs',JSON.stringify(nav)===JSON.stringify(['Memories','Agent','Settings']),nav);
  check('V2 has inline review filter within Memories',await main.locator('.mem-chips').getByRole('button',{name:'To review',exact:true}).count()===1);
 await main.locator('button.mem-row').filter({hasText:preference}).click();
 await main.locator('.mem-detail-text').filter({hasText:preference}).waitFor();
 const detailBounds=await main.locator('aside.mem-detail').boundingBox();
 check('English preference details appear in right sidebar',!!detailBounds&&detailBounds.x>400&&detailBounds.width>250,detailBounds);
 check('User memory text is unchanged',await main.locator('.mem-detail-text').innerText()===preference);
 result.main_viewport=await main.evaluate(()=>({width:innerWidth,height:innerHeight,dpr:devicePixelRatio}));
 check('Main uses V2 default native dimensions',result.main_viewport.width===1040&&Math.abs(result.main_viewport.height-680)<=4,result.main_viewport);
 await capture(main,'omna-v2-memories-en.png');
 await flyout.waitForFunction(()=>document.querySelector('.flyout-state')?.textContent.includes('Running'));
 check('Real flyout owner authentication and backend polling work',await flyout.locator('.flyout-state').innerText()==='Running');
 // A hidden BrowserWindow cannot reliably supply a screenshot. Its required authentication and live language checks still run below.
 result.not_run.push('Screenshot of hidden flyout webContents');
 await main.getByRole('button',{name:'Settings',exact:true}).click();
 await main.getByRole('combobox',{name:'Language'}).selectOption('zh-CN');
 await flyout.waitForFunction(()=>document.documentElement.lang==='zh-CN');
 check('Settings switches main window and existing flyout to Chinese',await main.evaluate(()=>document.documentElement.lang==='zh-CN')&&await flyout.locator('.flyout-state').innerText()==='运行中');
 check('Native locale JSON persists Chinese',JSON.parse(fs.readFileSync(path.join(userData,'locale.json'),'utf8')).locale==='zh-CN');
 await stop();check('Original Electron and service quit before Chinese restart',true);
 await launch();
 check('Actual app restart retains native Chinese in both windows',await main.evaluate(()=>window.omna.locale==='zh-CN'&&document.documentElement.lang==='zh-CN')&&await flyout.evaluate(()=>window.omna.locale==='zh-CN'&&document.documentElement.lang==='zh-CN'));
 await main.getByRole('button',{name:'设置',exact:true}).click();
 await main.getByRole('combobox',{name:'语言'}).selectOption('en');
 await flyout.waitForFunction(()=>document.documentElement.lang==='en');
 check('Settings restores main and existing flyout to English',await main.evaluate(()=>document.documentElement.lang==='en')&&await flyout.locator('.flyout-state').innerText()==='Running');
 check('Native locale JSON persists English',JSON.parse(fs.readFileSync(path.join(userData,'locale.json'),'utf8')).locale==='en');
 await stop();check('Original Electron and service quit before restart',true);
 await launch();
 check('Actual app restart retains native English in both windows',await main.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en')&&await flyout.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en'));
 await main.goto(origin+'/#/');await main.waitForFunction(()=>document.querySelectorAll('button.mem-row').length===10);
 check('Actual restart preserves ten real backend memories',(await api('/api/v1/memories',owner)).items.length===10&&await main.locator('button.mem-row').count()===10);
 await main.locator('button.mem-row').filter({hasText:preference}).click();await main.locator('.mem-detail-text').filter({hasText:preference}).waitFor();
 await stop();check('Final native test quit releases 8765',true);
 result.finished_at=new Date().toISOString();result.status='PASS';
})().catch(async error=>{result.status='FAIL';result.error=String(error);process.exitCode=1;try{await stop();result.failure_cleanup='PASS';}catch(cleanup){result.failure_cleanup=String(cleanup);}}).finally(()=>{fs.writeFileSync(path.join(out,'native-acceptance.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,checks:result.checks.length,error:result.error,result:path.join(out,'native-acceptance.json')}));});
