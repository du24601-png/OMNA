// Real V2 Windows package, bundled backend and kernel; isolated synthetic data.
const { chromium } = require('C:/Users/Ryan D/.cache/codex-runtimes/codex-primary-runtime/dependencies/node/node_modules/playwright');
const fs=require('node:fs'),path=require('node:path'),crypto=require('node:crypto'),net=require('node:net');
const {spawn,execFileSync}=require('node:child_process');
const root=path.resolve(__dirname,'..');
const out=path.join(__dirname,'results/desktop-locale-v2');
const run="D:\\zhiwo\\.claude\\worktrees\\omna-v2-upgrade-prep-9dab98\\.cache\\desktop-locale-v2\\1791274515120";
const userData=path.join(run,'user-data'),home=path.join(run,'home');
const appRoot=path.join(root,'apps/desktop/dist/english-20261006/win-unpacked');
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
 result.started_at=new Date().toISOString();result.source_worktree=root;result.branch=execFileSync('git',['branch','--show-current'],{cwd:root,encoding:'utf8'}).trim();result.head=execFileSync('git',['rev-parse','HEAD'],{cwd:root,encoding:'utf8'}).trim();result.package_version=JSON.parse(fs.readFileSync(path.join(root,'apps/desktop/package.json'),'utf8')).version;result.exe=exe;result.user_data=userData;result.client_home=home;result.prior_evidence='third-run-hidden-flyout-screenshot-failure.json';
 check('V2 2.0.0 source identity',result.branch==='v2'&&result.package_version==='2.0.0'&&result.head==='2420e03502423c6d6ff8849eaed80a3519212c99');
 check('8765 initially free',await freePort(8765)===8765);
 await launch();
 check('Actual main and native flyout loaded English',await main.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en')&&await flyout.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en'));
 await flyout.waitForFunction(()=>document.querySelector('.flyout-state')?.textContent.includes('Running'));
 check('Flyout authenticates to actual backend',await flyout.locator('.flyout-state').innerText()==='Running');
 await main.getByRole('button',{name:'Settings',exact:true}).click();
 await main.getByRole('combobox',{name:'Language'}).selectOption('zh-CN');
 await flyout.waitForFunction(()=>document.documentElement.lang==='zh-CN');
 check('Chinese instantly applies to both existing windows',await main.evaluate(()=>document.documentElement.lang==='zh-CN')&&await flyout.locator('.flyout-state').innerText()==='运行中');
 check('Chinese persisted in native locale JSON',JSON.parse(fs.readFileSync(path.join(userData,'locale.json'),'utf8')).locale==='zh-CN');
 await stop();check('Quit before Chinese restart releases service',true);
 await launch();
 check('Actual restart retains Chinese in both windows',await main.evaluate(()=>window.omna.locale==='zh-CN'&&document.documentElement.lang==='zh-CN')&&await flyout.evaluate(()=>window.omna.locale==='zh-CN'&&document.documentElement.lang==='zh-CN'));
 await main.getByRole('button',{name:'设置',exact:true}).click();
 await main.getByRole('combobox',{name:'语言'}).selectOption('en');
 await flyout.waitForFunction(()=>document.documentElement.lang==='en');
 check('English instantly restored to both existing windows',await main.evaluate(()=>document.documentElement.lang==='en')&&await flyout.locator('.flyout-state').innerText()==='Running');
 check('English persisted in native locale JSON',JSON.parse(fs.readFileSync(path.join(userData,'locale.json'),'utf8')).locale==='en');
 await stop();check('Quit before English restart releases service',true);
 await launch();
 check('Actual app restart retains English in both windows',await main.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en')&&await flyout.evaluate(()=>window.omna.locale==='en'&&document.documentElement.lang==='en'));
 const owner=fs.readFileSync(path.join(userData,'owner.credential'),'utf8').trim();
 await main.goto(origin+'/#/');await main.waitForFunction(()=>document.querySelectorAll('button.mem-row').length===10);
 check('Restart preserves ten real backend memories',(await api('/api/v1/memories',owner)).items.length===10&&await main.locator('button.mem-row').count()===10);
 await main.locator('button.mem-row').filter({hasText:preference}).click();await main.locator('.mem-detail-text').filter({hasText:preference}).waitFor();
 check('Actual V2 inline sidebar still shows unchanged memory',await main.locator('.mem-detail-text').innerText()===preference);
 await stop();check('Final native app and service quit releases 8765',true);
 result.finished_at=new Date().toISOString();result.status='PASS';
})().catch(async error=>{result.status='FAIL';result.error=String(error);process.exitCode=1;try{await stop();result.failure_cleanup='PASS';}catch(cleanup){result.failure_cleanup=String(cleanup);}}).finally(()=>{fs.writeFileSync(path.join(out,'native-continuation.json'),JSON.stringify(result,null,2));console.log(JSON.stringify({status:result.status,checks:result.checks.length,error:result.error,result:path.join(out,'native-continuation.json')}));});