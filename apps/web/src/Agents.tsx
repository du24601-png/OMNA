import { useEffect, useRef, useState } from "react"
import { api, explain, type AccessDetail, type AgentConnection } from "./api"
import { CATEGORIES, TOOLS, TOOL_DESCRIPTIONS, categoryLabel, connectionStatus, dateLabel, deliveryLabel, outcomeLabel, toolLabel } from "./format"
import { ClientBoard } from "./Clients"
import { canLeave, Empty, Notice, PageTitle, ResourceNotice, useResource, useUnsaved } from "./ui"
import type { Service } from "./App"

export function AgentPage({ tick, online, runtime }: { tick: number; online: boolean; runtime?: Service["runtime"] }) {
  const [localTick, setLocalTick] = useState(0)
  const resource = useResource(() => api.agents(), [tick, localTick])
  const [selected, setSelected] = useState<string | null>(null)
  const [name, setName] = useState("")
  const [showCreate, setShowCreate] = useState(false)
  const [issued, setIssued] = useState<AgentConnection | null>(null)
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const requests = useRef(new Map<string,string>())
  const running = useRef(false)
  const agents = resource.data?.agents || []
  const current = agents.find(a => a.id === selected) || agents[0]
  useUnsaved(!!name.trim(), busy)
  const refresh = () => { setLocalTick(n => n+1); resource.reload() }
  function keyFor(sig: string) { let key = requests.current.get(sig); if (!key) { key = crypto.randomUUID(); requests.current.set(sig,key) }; return key }
  async function create() {
    if (!name.trim() || running.current) return
    const sig = `create:${name.trim()}`
    running.current=true; setBusy(true);setError("");setNotice("")
    try { const value = await api.createAgent(name.trim(),keyFor(sig)); requests.current.delete(sig); setIssued(value);setName("");setShowCreate(false);setSelected(value.id);refresh() }
    catch (err) { setError(explain(err)) }
    finally { running.current=false;setBusy(false) }
  }
  async function patch(agent: AgentConnection, body: Record<string,unknown>) {
    if (running.current) return null
    const sig = `${agent.id}:${JSON.stringify(body)}`
    running.current=true;setBusy(true);setError("");setNotice("")
    try { const value=await api.updateAgent(agent.id,body,keyFor(sig));requests.current.delete(sig);setNotice("连接设置已保存。");refresh();return value }
    catch(err) {setError(explain(err));return null}
    finally {running.current=false;setBusy(false)}
  }
  async function rotate(agent: AgentConnection) {
    if (running.current || !window.confirm("重置后，旧凭证立即失效。客户端需要换成新配置才能继续调用，确定重置吗？")) return
    const sig=`rotate:${agent.id}`
    running.current=true;setBusy(true);setError("")
    try { const value=await api.rotateAgent(agent.id,keyFor(sig));requests.current.delete(sig);setIssued(value);refresh() }
    catch(err) {setError(explain(err))}
    finally {running.current=false;setBusy(false)}
  }
  return <div className="page"><PageTitle title="我的 Agent" description="从已安装的客户端里选一个写入配置，它只能读取你允许的内容。"><button className="button primary" disabled={busy} onClick={() => setShowCreate(!showCreate)}>创建连接</button></PageTitle>
    <ClientBoard online={online && !resource.error} onConnected={id => { setSelected(id); setLocalTick(value => value + 1) }} />
    <ResourceNotice resource={resource}/>
    {(showCreate || (resource.data && !agents.length && !resource.loading && !resource.error)) && <form className="create-connection surface" onSubmit={e => {e.preventDefault();void create()}}><div><h2>给这个连接起个名字</h2><p className="helper">例如“林舟的写作助手”。新连接默认没有任何权限。</p></div><div className="actions"><input aria-label="连接名称" placeholder="连接名称" value={name} maxLength={80} onChange={e => setName(e.target.value)} disabled={busy}/><button className="button primary" disabled={!online || busy || !name.trim()}>{busy ? "创建中…" : "确认创建"}</button></div></form>}
    {error && <Notice tone="error">{error}</Notice>}{notice && <Notice tone="success">{notice}</Notice>}
    {issued?.credential && <IssuedCredential agent={issued} runtime={runtime} onDone={() => setIssued(null)}/>}
    {resource.data && !agents.length && !resource.loading && !resource.error && <Empty title="你来决定谁能了解你">在上方选择已安装的客户端并写入配置，或创建一条自定义连接。</Empty>}
    {!!agents.length && <div className="agent-layout"><div className="agent-list">{agents.map(agent => <button key={agent.id} className={`agent-card ${agent.id===current?.id ? "selected" : ""}`} onClick={() => {if(agent.id!==current?.id && canLeave()) setSelected(agent.id)}}><span className="agent-avatar">{Array.from(agent.name)[0]}</span><strong>{agent.name}</strong><span className={`status-pill ${!agent.enabled ? "disabled" : agent.client_status==="verified" ? "verified" : ""}`}>{connectionStatus(agent)}</span><p>{agent.enabled ? "已启用" : "已停止访问"} · {agent.client_status==="verified" ? "曾验证成功" : "尚未验证"}</p><span className="helper">可读取：{agent.allowed_categories.length ? agent.allowed_categories.map(categoryLabel).join("、") : "未授权任何类别"}</span></button>)}</div>{current && <AgentEditor key={current.id} agent={current} online={online && !resource.error} busy={busy} tick={tick+localTick} onSave={body=>patch(current,body)} onRotate={()=>void rotate(current)}/>}</div>}
  </div>
}
function IssuedCredential({ agent,runtime,onDone }: {agent:AgentConnection;runtime?:Service["runtime"];onDone:()=>void}) {
  const [copied,setCopied]=useState(false),[error,setError]=useState("")
  const config=runtime ? JSON.stringify({mcp:{zhiwo:{type:"local",command:runtime.command,environment:{...runtime.environment,ZHIWO_AGENT_CREDENTIAL:agent.credential}}}},null,2) : ""
  return <section className="credential-box surface"><h2>保存这次连接配置</h2><p className="helper">凭证只在本次创建或重置后显示。关闭后不能再次查看，请妥善保存。</p>{runtime ? <><details className="disclosure"><summary>查看 OpenCode 配置（含专用凭证）</summary><pre className="code-block">{config}</pre></details><div className="actions"><button className="button primary" onClick={async()=>{try {await navigator.clipboard.writeText(config);setCopied(true);setError("")} catch {setError("复制失败，请展开配置后手动复制。")}}}>{copied ? "已复制配置" : "复制配置"}</button><button className="button secondary" onClick={onDone}>已保存，隐藏凭证</button></div></> : <><Notice tone="warning">无法读取客户端启动信息，请恢复服务后复制完整配置。凭证仍保留在当前界面。</Notice><details className="disclosure"><summary>查看本次专用凭证</summary><pre className="code-block">{agent.credential}</pre></details><button className="button secondary" onClick={onDone}>隐藏凭证</button></>}{error && <Notice tone="error">{error}</Notice>}</section>
}
function AgentEditor({agent,online,busy,tick,onSave,onRotate}:{agent:AgentConnection;online:boolean;busy:boolean;tick:number;onSave:(body:Record<string,unknown>)=>Promise<AgentConnection|null>;onRotate:()=>void}) {
  const [tools,setTools]=useState(agent.allowed_tools),[categories,setCategories]=useState(agent.allowed_categories)
  const [baseline,setBaseline]=useState(JSON.stringify([agent.allowed_tools,agent.allowed_categories]))
  const [eventId,setEventId]=useState<string|null>(null)
  const dirty=JSON.stringify([tools,categories])!==baseline
  useUnsaved(dirty,busy)
  useEffect(()=>{if(!dirty){setTools(agent.allowed_tools);setCategories(agent.allowed_categories);setBaseline(JSON.stringify([agent.allowed_tools,agent.allowed_categories]))}},[agent])
  const events=useResource(()=>api.accessEvents(agent.id),[agent.id,tick])
  const detail=useResource(()=>eventId ? api.accessEvent(eventId) : Promise.resolve(null),[eventId,tick])
  function toggle(list:string[],value:string,set:(next:string[])=>void){set(list.includes(value)?list.filter(x=>x!==value):[...list,value])}
  return <section className="agent-editor surface"><header><div><span className="eyebrow">连接与权限</span><h2>{agent.name}</h2></div><span className={`status-pill ${!agent.enabled?"disabled":""}`}>{connectionStatus(agent)}</span></header><p className="helper">验证成功仅表示曾完成一次客户端调用，不代表客户端此刻在线。</p>
    <details className="disclosure connection-guide"><summary>连接与验证指引</summary><ol><li>在上方列表里选择已安装的客户端，确认权限后写入配置。</li><li>打开那个客户端，请求一次已获准的记忆。</li><li>回到这里刷新访问记录。配置已写入还不代表验证成功。</li></ol><p className="helper">刷新连接只重读配置文件还在不在。已隐藏的凭证需重新写入后才会更新到客户端。</p><button className="button secondary" disabled={!online} onClick={()=>events.reload()}>刷新访问记录</button></details>
    <fieldset className="permissions" disabled={busy || !online}><legend>允许读取哪些内容</legend><p className="helper">只提供已确认、当前有效且允许 Agent 读取的记忆。</p><div className="category-permissions">{CATEGORIES.map(([id,label])=><label key={id}><input type="checkbox" checked={categories.includes(id)} onChange={()=>toggle(categories,id,setCategories)}/>{label}</label>)}</div></fieldset>
    <fieldset className="permissions" disabled={busy || !online}><legend>允许执行哪些操作</legend><div className="tool-permissions">{TOOLS.map(([id,label])=><label key={id}><input type="checkbox" checked={tools.includes(id)} onChange={()=>toggle(tools,id,setTools)}/><span><strong>{label}</strong><small>{TOOL_DESCRIPTIONS[id]}</small></span></label>)}</div></fieldset>
    <p className="permission-note">提出的修改建议需要你确认后才会生效。</p><div className="actions"><button className="button primary" disabled={busy || !online || !dirty} onClick={async()=>{const value=await onSave({allowed_tools:tools,allowed_categories:categories});if(value){setTools(value.allowed_tools);setCategories(value.allowed_categories);setBaseline(JSON.stringify([value.allowed_tools,value.allowed_categories]))}}}>{busy?"保存中…":"保存授权"}</button>{dirty && <span className="helper">有尚未保存的权限修改</span>}</div>
    <section className="access-section"><div className="section-heading"><h3>最近访问</h3><button className="text-button" onClick={events.reload}>刷新</button></div><p className="helper">记录实际返回的内容与交付状态，不表示模型已阅读或采用。</p><ResourceNotice resource={events}/>{events.data && !events.data.events.length && !events.error && !events.loading && <p className="inline-empty">还没有访问记录。在客户端发起一次请求后，可在这里查看。</p>}<div className="access-list">{events.data?.events.map(event=><button key={event.id} className={`access-row ${event.id===eventId?"selected":""}`} onClick={()=>setEventId(event.id)}><span><strong>{toolLabel(event.tool)}</strong><small>{agent.name} · {dateLabel(event.created_at)}</small></span><span><span>{event.versions.length} 条记忆 · {outcomeLabel(event.outcome)}</span><small>{deliveryLabel(event.delivery_state)}</small></span></button>)}</div>{eventId && <><ResourceNotice resource={detail}/>{detail.data?.id===eventId && <AccessPane detail={detail.data}/>}</>}</section>
    <section className="connection-controls"><h3>连接管理</h3><p className="helper">停用后拒绝后续请求；重置凭证会让旧配置立即失效。</p><div className="actions"><button className="button secondary" disabled={busy || !online} onClick={()=>void onSave({enabled:!agent.enabled})}>{agent.enabled?"停用连接":"启用连接"}</button><button className="button danger" disabled={busy || !online} onClick={onRotate}>重置凭证</button></div></section>
  </section>
}
function AccessPane({detail}:{detail:AccessDetail}) {
  const items=detail.response.items||[], explained=detail.response.result
  return <div className="access-detail"><h3>这次实际返回的内容</h3><p className="helper">{dateLabel(detail.created_at)} · {deliveryLabel(detail.delivery_state)}</p>{detail.response.error && <Notice tone="warning">{detail.response.error.message}</Notice>}{items.map(item=><article key={`${item.id}-${item.revision}`}><p className="prose">{item.content}</p><div className="metadata"><span>版本 {item.revision}</span><span>{item.scope || "未限定场景"}</span></div></article>)}{explained && <article><p className="prose">{explained.evidence || "未返回证据片段。"}</p><p className="helper">版本 {explained.revision}</p></article>}{!items.length && !explained && !detail.response.error && <p className="helper">这次没有返回记忆正文。{detail.response.proposal_id ? "修改建议已进入待确认。" : ""}</p>}<details className="disclosure"><summary>技术信息</summary><pre className="code-block">{JSON.stringify({event_id:detail.id,request_id:detail.request_id,policy_version:detail.policy_version,response:detail.response},null,2)}</pre></details></div>
}

