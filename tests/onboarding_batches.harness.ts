// Driven by tests/onboarding_batches.py: runs the web app's own batch logic
// (apps/web/src/batch.ts over apps/web/src/api.ts) against a throwaway local
// service. Synthetic data only. Prints one JSON object.
import "./onboarding_batches.shim"
import { ApiError, api, setCredential, type Proposal } from "../apps/web/src/api"
import { carryOut, choicesOf, loadLibrary, normalize, sortCandidates, undoBatches } from "../apps/web/src/batch"

const BASE = process.env.OMNA_BASE!
const AGENT_ID = process.env.OMNA_AGENT_ID!
const AGENT_TOKEN = process.env.OMNA_AGENT_TOKEN!
setCredential(process.env.OMNA_OWNER!)

const checks: { name: string; ok: boolean; detail?: unknown }[] = []
function check(name: string, ok: boolean, detail?: unknown) { checks.push({ name, ok, detail }) }
const keys = new Map<string, string>()
const keyFor = (sig: string) => { let key = keys.get(sig); if (!key) { key = crypto.randomUUID(); keys.set(sig, key) } return key }
const current = async () => (await api.memories({ state: "current", limit: 1 })).total ?? -1
const pending = async () => (await api.proposals()).proposals
const byText = (list: Proposal[], text: string) => list.find(item => item.payload.content === text)!

async function propose(content: string, category: string) {
  const response = await fetch(`${BASE}/api/v1/agent/tools/propose_memory`, {
    method: "POST",
    headers: { Authorization: `Bearer ${AGENT_TOKEN}`, "Content-Type": "application/json" },
    body: JSON.stringify({ change: { type: "add", content, kind: "fact", category }, evidence: { text: content } }),
  })
  if (!response.ok) throw new Error(`propose ${response.status} ${await response.text()}`)
}

async function organizeFlow() {
  const before = await current()
  const agent = (await api.agents()).agents.find(item => item.id === AGENT_ID)!
  check("organize: agent starts read-only", !agent.allowed_tools.includes("propose_memory"), agent.allowed_tools)
  // The card's consent step: allow proposing, then open the session.
  const wanted = new Set([...agent.allowed_tools, "propose_memory"])
  await api.updateAgent(agent.id, { allowed_tools: ["get_context", "search_memory", "propose_memory", "explain_memory"].filter(id => wanted.has(id)), propose_categories: ["identity", "goal", "preference", "project", "event", "other"] }, keyFor("allow"))
  const session = await api.organize(agent.id)
  check("organize: session prompt comes from the service", session.prompt.includes("OMNA"), session.prompt)
  await propose("Python 项目用 uv 管理依赖", "preference")
  await propose("SQL 关键字用大写", "preference")
  await propose("先给结论再讲原因，不需要客套", "preference")
  await propose("在做数据管道重构，月底上线", "project")
  const library = await loadLibrary()
  const seen = sortCandidates(await pending(), new Set([session.batch_id]), library)
  const lanes = Object.fromEntries(seen.map(item => [item.proposal.payload.content, item.lane]))
  check("organize: four suggestions land in the batch", seen.length === 4, lanes)
  check("organize: the one like an existing memory is held back", lanes["先给结论再讲原因，不需要客套"] === "similar", lanes)
  // A suggestion that arrives after the owner looked must not be remembered unseen.
  await propose("周报习惯在周五下午写", "preference")
  const plain = seen.filter(item => item.lane === "plain")
  const keep = plain.filter(item => item.proposal.payload.content !== "在做数据管道重构，月底上线")
  const drop = plain.filter(item => item.proposal.payload.content === "在做数据管道重构，月底上线")
  const outcome = await carryOut({ keep, drop, categories: {}, choices: [], closed: new Set() }, keyFor)
  check("organize: remembers exactly the two kept", outcome.accepted === 2 && outcome.rejected === 1 && !outcome.errors.length, outcome)
  check("organize: library grew by 2", (await current()) === before + 2)
  const left = (await pending()).filter(item => item.batch_id === session.batch_id).map(item => item.payload.content).sort()
  check("organize: late arrival and the similar one stay pending", JSON.stringify(left) === JSON.stringify(["先给结论再讲原因，不需要客套", "周报习惯在周五下午写"].sort()), left)
  const summary = await api.batch(session.batch_id)
  check("organize: batch is undoable", summary.undoable && summary.remembered === 2, summary)
  const deleted = await undoBatches([session.batch_id])
  check("organize: undo deletes the 2", deleted === 2, deleted)
  check("organize: library back to before", (await current()) === before)
}

async function importFlow() {
  const before = await current()
  const files = (await api.agentFiles()).files
  const file = files.find(item => item.id === "claude-code")
  check("import: whitelisted CLAUDE.md is listed, not yet imported", !!file && !file.imported_at && !file.empty, files)
  const job = await api.importSource({ kind: "agent_file", file_id: "claude-code" }, keyFor("import"))
  check("import: split without a model, one exact duplicate merged", job.method === "split" && job.merged_duplicates === 1, { status: job.status, method: job.method, merged: job.merged_duplicates })
  const library = await loadLibrary()
  const seen = sortCandidates(await pending(), new Set([job.job_id]), library)
  const choices = choicesOf(seen)
  const plain = seen.filter(item => item.lane === "plain")
  check("import: the two near-identical lines become one choice", choices.length === 1 && !!choices[0].second, choices.map(choice => [choice.first.proposal.payload.content, choice.second?.proposal.payload.content]))
  check("import: three plain additions", plain.length === 3, plain.map(item => item.proposal.payload.content))
  const vitest = plain.find(item => item.proposal.payload.content.includes("Vitest"))!
  const paper = plain.find(item => item.proposal.payload.content.includes("纸鸢"))!
  const keep = plain.filter(item => item !== paper)
  const plan = { keep, drop: [paper], categories: { [vitest.proposal.id]: "other" }, choices: [{ choice: choices[0], pick: "second" as const }], closed: new Set([job.job_id]) }
  const outcome = await carryOut(plan, keyFor)
  check("import: 2 kept + the picked one remembered, 2 rejected", outcome.accepted === 3 && outcome.rejected === 2 && !outcome.errors.length, outcome)
  check("import: library grew by 3", (await current()) === before + 3)
  const other = (await api.memories({ state: "current", category: "other", limit: 50 })).items.map(item => item.content)
  check("import: changed category is saved", other.some(text => text?.includes("Vitest")), other)
  const again = await carryOut(plan, keyFor)
  check("import: repeating the same plan publishes nothing new", (await current()) === before + 3 && !again.errors.length, again)
  check("import: nothing of this import left pending", !(await pending()).some(item => item.batch_id === job.job_id))
  const deleted = await undoBatches([job.job_id])
  check("import: undo deletes the 3", deleted === 3, deleted)
  check("import: library back to before", (await current()) === before)
  const after = (await api.agentFiles()).files.find(item => item.id === "claude-code")
  check("import: after undo the file reads as not imported", !!after && !after.imported_at, after)
}

function similarityEdges() {
  check("normalize: strips nested Markdown like split.normalize", normalize(" **`ＰＮＰＭ`**， ") === "pnpm")
  const make = (id: string, content: string) => ({ id, status: "pending", change_type: "add", target_id: null, batch_id: "synthetic", payload: { content, category: "preference" }, evidence: { text: content } } as Proposal)
  const pair = [make("one", "git 提交信息用中文，动词开头"), make("two", "git 提交信息用中文，动词开头写")]
  const choices = choicesOf(sortCandidates(pair, new Set(["synthetic"]), []))
  check("organize: unmarked near additions form one pair", choices.length === 1 && !!choices[0].second, choices)
}

async function extraFlows() {
  const before = await current()
  for (const pick of ["first", "both"] as const) {
    const job = await api.importSource({ kind: "paste", text: "# 偏好\n- git 提交信息用中文，动词开头\n- git 提交信息用中文，动词开头写" }, keyFor(`pair:${pick}`))
    const choice = choicesOf(sortCandidates(await pending(), new Set([job.job_id]), await loadLibrary()))[0]
    if (!choice?.second) throw new Error("extra pair fixture was not paired")
    const result = await carryOut({ keep: [], drop: [], categories: {}, choices: [{ choice, pick }], closed: new Set([job.job_id]) }, keyFor)
    const n = pick === "both" ? 2 : 1
    check(`import: pair ${pick} retains exactly ${n}`, result.accepted === n && !result.errors.length && await current() === before + n, result)
    await undoBatches([job.job_id])
  }
  const none = await api.importSource({ kind: "paste", text: "# 目标\n- 每周学习两小时统计学\n- 今年完成数据分析课程" }, keyFor("none"))
  const drop = sortCandidates(await pending(), new Set([none.job_id]), await loadLibrary()).filter(item => item.lane === "plain")
  const result = await carryOut({ keep: [], drop, categories: {}, choices: [], closed: new Set([none.job_id]) }, keyFor)
  check("import: all unchecked rejects without publishing", drop.length === 2 && result.rejected === 2 && result.accepted === 0 && await current() === before, result)
  const session = await api.organize(AGENT_ID)
  await propose("周末坚持户外徒步", "preference")
  const keep = sortCandidates(await pending(), new Set([session.batch_id]), await loadLibrary()).filter(item => item.lane === "plain")
  const plan = { keep, drop: [], categories: {}, choices: [], closed: new Set<string>() }
  const real = globalThis.fetch
  let lost = false
  globalThis.fetch = async (input, init) => {
    const response = await real(input, init)
    if (!lost && String(input).includes(`/proposals/${keep[0].proposal.id}/decision`)) { lost = true; throw new TypeError("synthetic lost response after commit") }
    return response
  }
  let failed
  try { failed = await carryOut(plan, keyFor) } finally { globalThis.fetch = real }
  check("retry: response loss is observable", lost && !!failed.errors.length && await current() === before + 1, failed)
  const retried = await carryOut(plan, keyFor)
  check("retry: same request after response loss creates no duplicate", !retried.errors.length && await current() === before + 1, retried)
  await propose("周五下午安排读书时间", "preference")
  const added = sortCandidates(await pending(), new Set([session.batch_id]), await loadLibrary()).filter(item => item.lane === "plain")
  await carryOut({ keep: added, drop: [], categories: {}, choices: [], closed: new Set() }, keyFor)
  const member = (await loadLibrary()).find(item => item.content === "周末坚持户外徒步")!
  await api.updateMemory(member.id, { content: "周末坚持户外徒步，每次两小时", kind: "fact", category: "preference", base_revision: member.revision, source_refs: [] }, keyFor("edit-after"))
  let conflict = false
  try { await undoBatches([session.batch_id]) } catch (err) { conflict = err instanceof ApiError && err.code === "CONFLICT" }
  check("undo: changed member refuses whole batch and deletes none", conflict && await current() === before + 2)
}

try {
  similarityEdges()
  await organizeFlow()
  await importFlow()
  await extraFlows()
} catch (err) {
  check("no unexpected error", false, String(err instanceof Error ? `${err.message}` : err))
}
console.log(JSON.stringify({ pass: checks.every(item => item.ok), checks }))
