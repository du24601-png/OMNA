// Shared by onboarding step 3 and the connect-import card: sort one or more
// batches' pending suggestions into plain additions, near duplicates and the
// ones that need one-by-one review, then carry out what the owner picked.
import { ApiError, api, type AgentFile, type Memory, type Proposal } from "./api"

export type Lane = "plain" | "similar" | "review"
export type Candidate = {
  proposal: Proposal
  batch: string
  lane: Lane
  // For "similar": the memory or the other candidate it looks like.
  like?: { content: string; memoryId?: string; proposalId?: string }
  // For "review": why it can't be remembered in one click.
  reason?: "update" | "long" | "evidence"
}

export const BATCH_POLL_MS = 4000
// No arrivals for this long after the first one reads as "done for now".
export const QUIET_MS = 20000

export function batchOf(proposal: Proposal) {
  return proposal.batch_id || null
}

export function sortCandidates(proposals: Proposal[], batches: Set<string>, library: Memory[]): Candidate[] {
  const mine = proposals.filter(item => item.status === "pending" && batches.has(batchOf(item) || ""))
  const out: Candidate[] = mine.map(proposal => {
    const batch = batchOf(proposal)!
    const content = proposal.payload.content || ""
    if (proposal.target_id || proposal.change_type !== "add") return { proposal, batch, lane: "review", reason: "update" }
    if (content.length > 2000) return { proposal, batch, lane: "review", reason: "long" }
    if (!proposal.evidence?.text?.trim()) return { proposal, batch, lane: "review", reason: "evidence" }
    const remembered = library.find(memory => memory.content && (normalize(memory.content) === normalize(content) || (memory.content.length >= 8 && overlap(content, memory.content) >= SIMILAR)))
    if (remembered) return { proposal, batch, lane: "similar", like: { content: remembered.content!, memoryId: remembered.id } }
    const marked = proposal.payload.similar_to
    if (marked?.content) return { proposal, batch, lane: "similar", like: { content: marked.content, memoryId: marked.memory_id } }
    return { proposal, batch, lane: "plain" }
  })
  // A candidate marked like another candidate of the same import: point at it,
  // and take that other one out of the plain list so the owner picks between the two.
  for (const item of out) {
    if (item.lane !== "similar" || item.like?.memoryId) continue
    const twin = out.find(other => other !== item && other.batch === item.batch && other.lane === "plain" && normalize(other.proposal.payload.content) === normalize(item.like!.content))
    if (twin) { item.like = { content: twin.proposal.payload.content, proposalId: twin.proposal.id }; twin.lane = "similar"; twin.like = { content: item.proposal.payload.content, proposalId: item.proposal.id } }
  }
  // Agent proposals do not carry split.py's similar_to marker. Pair the
  // remaining plain additions too, without reusing a candidate in two pairs.
  for (const item of out) {
    if (item.lane !== "plain") continue
    const twin = out.find(other => other !== item && other.batch === item.batch && other.lane === "plain" && other.proposal.payload.content.length >= 8 && overlap(item.proposal.payload.content, other.proposal.payload.content) >= SIMILAR)
    if (!twin) continue
    item.lane = "similar"; twin.lane = "similar"
    item.like = { content: twin.proposal.payload.content, proposalId: twin.proposal.id }
    twin.like = { content: item.proposal.payload.content, proposalId: item.proposal.id }
  }
  return out
}

// One similar pair (or one candidate against a remembered memory) per entry,
// listed once even when both members of a pair are candidates.
export type Choice = { id: string; first: Candidate; second?: Candidate; memory?: string }
export function choicesOf(candidates: Candidate[]): Choice[] {
  const seen = new Set<string>()
  const out: Choice[] = []
  for (const item of candidates) {
    if (item.lane !== "similar" || seen.has(item.proposal.id)) continue
    seen.add(item.proposal.id)
    const second = item.like?.proposalId ? candidates.find(other => other.proposal.id === item.like!.proposalId) : undefined
    if (second) seen.add(second.proposal.id)
    out.push({ id: item.proposal.id, first: item, second, memory: second ? undefined : item.like?.content })
  }
  return out
}

// "first" / "second" keep only that one; "both" keeps both; "memory" keeps
// only the remembered memory (the candidate is not remembered).
export type Pick = "first" | "second" | "both" | "memory"

export type Plan = {
  keep: Candidate[]
  drop: Candidate[]
  categories: Record<string, string>
  choices: { choice: Choice; pick: Pick }[]
  // Import batches are a closed set, so their plain additions can go through
  // accept-additions. Organize batches can still grow while the owner looks,
  // so those are decided one by one: nothing unseen gets remembered.
  closed: Set<string>
}

export type Outcome = { accepted: number; rejected: number; left: number; errors: string[]; categories: Set<string> }

export async function carryOut(plan: Plan, keyFor: (sig: string) => string): Promise<Outcome> {
  const outcome: Outcome = { accepted: 0, rejected: 0, left: 0, errors: [], categories: new Set() }
  const accept: { item: Candidate; category?: string }[] = []
  const reject: Candidate[] = [...plan.drop]
  const bulk = new Map<string, Candidate[]>()
  for (const item of plan.keep) {
    const category = plan.categories[item.proposal.id]
    if (!category && plan.closed.has(item.batch)) bulk.set(item.batch, [...(bulk.get(item.batch) || []), item])
    else accept.push({ item, category })
  }
  for (const { choice, pick } of plan.choices) {
    const keepFirst = pick === "first" || pick === "both"
    const keepSecond = !!choice.second && (pick === "second" || pick === "both")
    if (keepFirst) accept.push({ item: choice.first }); else reject.push(choice.first)
    if (choice.second) { if (keepSecond) accept.push({ item: choice.second }); else reject.push(choice.second) }
  }
  const fresh = bulk.size ? (await api.proposals()).proposals : []
  for (const [batch, items] of bulk) {
    const wanted = new Set(items.map(item => item.proposal.id))
    const exclude = fresh.filter(item => batchOf(item) === batch && !wanted.has(item.id)).map(item => item.id)
    try {
      const result = await api.acceptAdditions(batch, exclude)
      for (const row of result.results) {
        if (!wanted.has(row.proposal_id)) continue
        if (row.status === "accepted") { outcome.accepted++; outcome.categories.add(items.find(item => item.proposal.id === row.proposal_id)!.proposal.payload.category) }
        else if (row.status === "skipped") outcome.left++
        else outcome.errors.push(row.message || "有一条没有记住，可以重试。")
      }
    } catch (err) { outcome.errors.push(describe(err)) }
  }
  for (const { item, category } of accept) {
    const body = category ? { decision: "edit", content: item.proposal.payload.content, category } : { decision: "accept" }
    try {
      await api.decide(item.proposal.id, body, keyFor(`${item.proposal.id}:${JSON.stringify(body)}`))
      outcome.accepted++
      outcome.categories.add(category || item.proposal.payload.category)
    } catch (err) { outcome.errors.push(describe(err)) }
  }
  for (const item of reject) {
    try {
      await api.decide(item.proposal.id, { decision: "reject" }, keyFor(`${item.proposal.id}:reject`))
      outcome.rejected++
    } catch (err) { outcome.errors.push(describe(err)) }
  }
  return outcome
}

function describe(err: unknown) {
  if (err instanceof ApiError && (err.status === 0 || err.code === "UNAVAILABLE")) return "本地服务未运行，没记住的还在，可以重试。"
  return err instanceof Error && err.message ? err.message : "有一条没有完成，可以重试。"
}

// Undo every batch that produced memories. Batches with nothing to undo are fine.
export async function undoBatches(batches: string[]) {
  let deleted = 0
  for (const batch of batches) {
    try { deleted += (await api.undoBatch(batch)).deleted }
    catch (err) { if (!(err instanceof ApiError && err.code === "NOT_FOUND")) throw err }
  }
  return deleted
}

// Current memories, a page at a time, for the near-duplicate check.
export async function loadLibrary(limit = 500): Promise<Memory[]> {
  const out: Memory[] = []
  let cursor: string | undefined
  while (out.length < limit) {
    const page = await api.memories({ state: "current", limit: 50, cursor })
    out.push(...page.items)
    if (!page.next_cursor) break
    cursor = page.next_cursor
  }
  return out
}

// Same rules as the server's split.normalize / overlap (threshold 0.62), kept
// close so the owner sees the same pairs the server would hold back.
const SIMILAR = 0.62
export function normalize(text: string) {
  let plain = text.trim(), previous: string
  do { previous = plain; plain = plain.replace(/(\*\*|__|\*|_|`)(.+?)\1/g, "$2") } while (plain !== previous)
  return plain.normalize("NFKC").toLowerCase().split(/\s+/).filter(Boolean).join(" ").replace(/[。．.！!？?；;，,、\s]+$/u, "").trim()
}
export function overlap(left: string, right: string) {
  const a = grams(left), b = grams(right)
  if (a.size < 4 || b.size < 4) return 0
  if (left.length >= 12 && right.length >= 12 && (left.includes(right) || right.includes(left))) return 1
  let hit = 0
  for (const gram of a) if (b.has(gram)) hit++
  return hit / Math.min(a.size, b.size)
}
function grams(value: string) {
  const text = value.replace(/\s+/g, "")
  const out = new Set<string>()
  for (let index = 0; index < text.length - 1; index++) out.add(text.slice(index, index + 2))
  return out
}

// Instruction files: the whitelist lists a file's own client first; any
// other client there only falls back to it (OpenCode reads CLAUDE.md when it
// has no AGENTS.md of its own).
export function ownsFile(file: AgentFile, clientId: string) {
  return file.clients[0]?.id === clientId
}

export function fileForClient(files: AgentFile[], clientId: string) {
  const readable = files.filter(file => !file.empty && file.clients.some(item => item.id === clientId))
  return readable.find(file => ownsFile(file, clientId)) ?? readable[0]
}

// One organizer per file. `groups` are clients connected together, earliest
// first. Within a group each client takes its own file before anyone takes a
// fallback; across groups the earlier connection wins.
export function assignFiles(files: AgentFile[], groups: string[][]) {
  const taken = new Map<string, AgentFile>()
  const used = new Set<string>()
  const pick = (clientId: string, own: boolean) => {
    if (taken.has(clientId)) return
    const file = files.find(item => !item.empty && !used.has(item.id) && item.clients.some(c => c.id === clientId) && (!own || ownsFile(item, clientId)))
    if (file) { taken.set(clientId, file); used.add(file.id) }
  }
  for (const group of groups) {
    for (const id of group) pick(id, true)
    for (const id of group) pick(id, false)
  }
  return taken
}
