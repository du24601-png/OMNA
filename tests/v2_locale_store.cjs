const assert=require('node:assert/strict');
const fs=require('node:fs');
const os=require('node:os');
const path=require('node:path');
const {createLocaleStore}=require('../apps/desktop/src/locale.cjs');
const root=fs.mkdtempSync(path.join(os.tmpdir(),'omna-locale-synthetic-'));
const results=[];
const check=(name,fn)=>{try{fn();results.push({name,status:'PASS'});}catch(e){results.push({name,status:'FAIL',error:String(e)});}};
try{
 const file=path.join(root,'locale.json');
 check('missing preference defaults English',()=>assert.equal(createLocaleStore(file).get(),'en'));
 check('malformed preference defaults English',()=>{fs.writeFileSync(file,'broken JSON');assert.equal(createLocaleStore(file).get(),'en');});
 check('unsupported saved locale defaults English',()=>{fs.writeFileSync(file,JSON.stringify({locale:'fr'}));assert.equal(createLocaleStore(file).get(),'en');});
 const store=createLocaleStore(file);
 check('unsupported update does not write or change active locale',()=>{const before=fs.readFileSync(file,'utf8');assert.equal(store.set('fr'),false);assert.equal(store.get(),'en');assert.equal(fs.readFileSync(file,'utf8'),before);});
 check('Chinese saves and reloads',()=>{assert.equal(store.set('zh-CN'),true);assert.equal(store.get(),'zh-CN');assert.equal(createLocaleStore(file).get(),'zh-CN');assert.equal(store.text('中文','English'),'中文');});
 check('English saves and reloads',()=>{assert.equal(store.set('en'),true);assert.equal(createLocaleStore(file).get(),'en');assert.equal(store.text('中文','English'),'English');});
 check('failed write preserves active and saved locale',()=>{fs.mkdirSync(file+'.tmp');assert.equal(store.set('zh-CN'),false);assert.equal(store.get(),'en');assert.equal(createLocaleStore(file).get(),'en');fs.rmdirSync(file+'.tmp');});
 check('unrelated synthetic user content remains unchanged',()=>{const content=path.join(root,'synthetic-memory.txt');fs.writeFileSync(content,'我喜欢慢跑。');store.set('zh-CN');store.set('en');assert.equal(fs.readFileSync(content,'utf8'),'我喜欢慢跑。');});
}finally{fs.rmSync(root,{recursive:true,force:true});}
const result={platform:process.platform,status:results.every(r=>r.status==='PASS')?'PASS':'FAIL',checks:results};
fs.writeFileSync(path.join(__dirname,'results/locale-v2/native-store.json'),JSON.stringify(result,null,2));
console.log(JSON.stringify(result,null,2));
process.exitCode=result.status==='PASS'?0:1;
