// Raw V2 schema fixtures. No translated backend prose or real file/client paths.
const memory={id:'locale-synthetic',revision:1,content:'我喜欢周末在湖边慢跑。',kind:'fact',category:'preference',scope:'中文适用场景 {0}',lifecycle:'active',share_enabled:true,valid_until:null,source_ids:[],created_at:'2025-01-02T13:04:00Z'};
const proposal={id:'locale-proposal',status:'pending',decision:null,change_type:'add',target_id:null,base_revision:null,payload:{content:'周末我会去湖边慢跑。',kind:'fact',category:'preference',scope:null},evidence:{text:'合成证据，保留中文。'},source:{id:'locale-source',kind:'paste',name:null},batch_id:null,demo:true};
const agent={id:'locale-agent',name:'中文 Agent 名字 {0}',enabled:true,policy_version:1,allowed_tools:['get_context','search_memory','propose_memory','explain_memory'],allowed_categories:['preference','goal'],propose_categories:['preference'],client_status:'pending',last_access_at:null};
const client={id:'claude-code',name:'Claude Code',installed:true,configured:true,config_path:'synthetic-only/claude-config.json',agent_id:agent.id};
const file={id:'locale-file',clients:[{id:client.id,name:client.name}],path:'synthetic-only/CLAUDE.md',size:80,modified_at:'2025-01-02T13:04:00Z',empty:false,imported_at:null};
const fs=require('node:fs'),path=require('node:path');
const batchesSource=fs.readFileSync(path.join(__dirname,'../server/zhiwo/services/batches.py'),'utf8');
const promptBlock=batchesSource.match(/ORGANIZE_PROMPT = \(([\s\S]*?)\n\)/)[1];
const organizePrompt=[...promptBlock.matchAll(/"([^"\n]*)"/g)].map(m=>JSON.parse(m[0])).join('');
function fixture(url){
 const p=url.pathname;
 if(p==='/api/v1/health')return {status:'ok',embeddings_loaded:true,connect_only:false,extractor_configured:false,test_mode:true};
 if(p==='/api/v1/status')return {service:{ok:true,embeddings_loaded:true},pending:{count:1,latest_at:'2025-01-02T13:04:00Z'},sharing:{paused:false,paused_until:null},reads_today:{total:0,agents:[]},recent_reads:[]};
 if(p==='/api/v1/memories'){const c=url.searchParams.get('category');return {items:c&&c!==memory.category?[]:[memory],total:c&&c!==memory.category?0:1,origins:[],next_cursor:null};}
 if(p===`/api/v1/memories/${memory.id}`)return memory;
 if(p===`/api/v1/memories/${memory.id}/versions`)return {memory_id:memory.id,versions:[memory]};
 if(p==='/api/v1/proposals')return {proposals:[proposal]};
 if(p==='/api/v1/agents')return {agents:[agent]};
 if(p==='/api/v1/agent-clients')return {clients:[client]};
 if(p==='/api/v1/agent-files')return {files:[file]};
 if(p===`/api/v1/agents/${agent.id}/organize`)return {batch_id:'locale-batch',started_at:'2026-10-06T00:00:00Z',expires_at:'2099-01-01T00:00:00Z',agent_id:agent.id,prompt:organizePrompt};
 if(p==='/api/v1/access-events')return {events:[]};
 if(p==='/api/v1/batches')return {batches:[]};
 if(p==='/api/v1/settings')return {extractor:{base_url:'',model:'',key_saved:false,configured:false},data_dir:'synthetic-only/data',restart_required:false};
 return undefined;
}
module.exports={memory,proposal,agent,client,file,fixture,organizePrompt};
