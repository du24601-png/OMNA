// Just enough of a browser for apps/web/src/api.ts to run under Node:
// relative URLs go to the throwaway service named in OMNA_BASE.
const base = process.env.OMNA_BASE!
const store = new Map<string, string>()
const g = globalThis as Record<string, unknown>
g.window = { dispatchEvent: () => true }
g.sessionStorage = { getItem: (key: string) => store.get(key) ?? null, setItem: (key: string, value: string) => { store.set(key, value) }, removeItem: (key: string) => { store.delete(key) } }
const realFetch = globalThis.fetch
g.fetch = (input: string, init?: RequestInit) => realFetch(input.startsWith("/") ? base + input : input, init)
export {}
