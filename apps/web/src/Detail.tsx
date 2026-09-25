import { useEffect, useRef, useState } from "react"
import { api, explain, type Memory, type Source, type Version } from "./api"
import { categoryLabel, lifecycleLabel, sourceLabel } from "./format"

export function Detail({
  memoryId,
  onClose,
  onSaved,
}: {
  memoryId: string
  onClose: () => void
  onSaved: () => void
}) {
  const [memory, setMemory] = useState<Memory | null>(null)
  const [draft, setDraft] = useState("")
  const [onlySelf, setOnlySelf] = useState(false)
  const [versions, setVersions] = useState<Version[]>([])
  const [sources, setSources] = useState<Source[]>([])
  const [error, setError] = useState("")
  const [notice, setNotice] = useState("")
  const [busy, setBusy] = useState(false)
  const attempt = useRef<{ sig: string; key: string } | null>(null)

  function load() {
    setError("")
    Promise.all([api.memory(memoryId), api.versions(memoryId)])
      .then(async ([item, history]) => {
        setMemory(item)
        setDraft(item.content || "")
        setOnlySelf(item.share_enabled === false)
        setVersions(history.versions)
        const loaded = await Promise.all((item.source_ids || []).map((id) => api.source(id).catch(() => null)))
        setSources(loaded.filter((source): source is Source => source !== null))
      })
      .catch((err: unknown) => setError(explain(err)))
  }

  useEffect(() => {
    load()
  }, [memoryId])

  async function save() {
    if (!memory) return
    const body = {
      content: draft,
      kind: memory.kind,
      category: memory.category,
      scope: memory.scope ?? null,
      valid_until: memory.valid_until ?? null,
      share_enabled: !onlySelf,
      source_refs: memory.source_ids || [],
      base_revision: memory.revision,
    }
    const sig = JSON.stringify(body)
    if (!attempt.current || attempt.current.sig !== sig) attempt.current = { sig, key: crypto.randomUUID() }
    setBusy(true)
    setError("")
    setNotice("")
    try {
      const saved = (await api.updateMemory(memory.id, body, attempt.current.key)) as {
        status?: string
        revision?: number
      }
      if (saved?.status !== "accepted" || !saved.revision) {
        setError("服务没有确认这次修改。正式记忆不应视为已更新。")
        return
      }
      attempt.current = null
      setNotice("已保存。这次修改直接生效，没有进入待确认。")
      onSaved()
      load()
    } catch (err: unknown) {
      setError(explain(err))
    } finally {
      setBusy(false)
    }
  }

  return (
    <aside className="detail-pane flex h-full w-[360px] shrink-0 flex-col border-l border-line bg-white">
      <div className="flex items-center justify-between px-4 py-3">
        <h2 className="text-base font-semibold">详情</h2>
        <button className="rounded-lg px-2 py-1 text-muted hover:bg-paper" onClick={onClose} type="button">
          关闭
        </button>
      </div>
      <div className="min-h-0 flex-1 space-y-4 overflow-auto px-4 pb-6">
        {error ? (
          <p className="rounded-xl bg-[#fff4f2] px-3 py-2 text-sm text-[#9b2c2c]" role="alert">
            {error}
          </p>
        ) : null}
        {notice ? <p className="text-sm text-[#17663a]">{notice}</p> : null}
        {memory ? (
          <>
            <label className="block text-sm">
              记忆内容
              <textarea
                aria-label="纠正后的内容"
                className="mt-1 min-h-28 w-full rounded-xl border border-line px-3 py-2"
                onChange={(event) => setDraft(event.target.value)}
                value={draft}
              />
            </label>
            <label className="flex items-center gap-2 text-sm">
              <input checked={onlySelf} onChange={(event) => setOnlySelf(event.target.checked)} type="checkbox" />
              仅自己可见
            </label>
            <button
              className="rounded-xl bg-action px-3 py-2 text-white disabled:opacity-50"
              disabled={busy || !draft.trim()}
              onClick={save}
              type="button"
            >
              保存
            </button>
            <p className="text-sm text-muted">保存后立即生效，不会进入待确认。</p>
            <p className="text-sm text-muted">
              {categoryLabel(memory.category)} · 版本 {memory.revision}
              {memory.scope ? ` · ${memory.scope}` : ""}
              {memory.share_enabled === false ? " · 仅自己可见" : ""}
            </p>
            <p className="break-all text-xs text-muted">记忆 {memory.id}</p>
          </>
        ) : null}
        <section>
          <h3 className="mb-2 text-sm font-semibold">来源</h3>
          {sources.length === 0 ? <p className="text-sm text-muted">这条记忆没有关联来源。</p> : null}
          {sources.map((source) => (
            <article className="mb-3 rounded-xl bg-paper p-3" key={source.id}>
              <p className="mb-2 text-sm text-muted">
                {sourceLabel(source.kind)}
                {source.name ? ` · ${source.name}` : ""}
              </p>
              <pre className="whitespace-pre-wrap font-sans text-sm leading-6">{source.content}</pre>
            </article>
          ))}
        </section>
        <section>
          <h3 className="mb-2 text-sm font-semibold">版本</h3>
          <ol className="space-y-2">
            {versions.map((version) => (
              <li className="rounded-xl border border-line p-3" key={version.revision}>
                <p className="mb-1 text-sm text-muted">
                  版本 {version.revision} · {lifecycleLabel(version.lifecycle)}
                </p>
                <p className="whitespace-pre-wrap text-sm leading-6">{version.content}</p>
              </li>
            ))}
          </ol>
        </section>
      </div>
    </aside>
  )
}
