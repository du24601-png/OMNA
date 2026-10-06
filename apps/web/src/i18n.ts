import { useState, useSyncExternalStore } from "react"
import english from "./messages.en.json"

export type Locale = "en" | "zh-CN"
const KEY = "omna.locale"
const listeners = new Set<() => void>()
const messages: Record<string, string> = english

export function loadLocale(): Locale {
  if (typeof window === "undefined") return "en"
  const desktop = window.omna?.locale
  if (desktop === "en" || desktop === "zh-CN") return desktop
  try { return localStorage.getItem(KEY) === "zh-CN" ? "zh-CN" : "en" }
  catch { return "en" }
}

let locale: Locale = loadLocale()
export function getLocale() { return locale }
export function formatLocale() { return locale === "en" ? "en-US" : "zh-CN" }
const subscribe = (listener: () => void) => { listeners.add(listener); return () => { listeners.delete(listener) } }
export function useLocale() { return useSyncExternalStore(subscribe, getLocale, () => "en" as Locale) }

// Defer application notices so an existing error/status follows a language change too.
export function useMessage() {
  const [message, setMessage] = useState<string | (() => string)>("")
  return [typeof message === "function" ? message() : message,
    (next: string | (() => string)) => setMessage(() => next)] as const
}

export function applyLocale() {
  document.documentElement.lang = locale
}

function receiveLocale(next: unknown) {
  if (next !== "en" && next !== "zh-CN") return
  if (locale === next) return
  locale = next
  applyLocale()
  listeners.forEach(listener => listener())
}

// Native main owns persistence; other windows receive changes without writing back.
window.omna?.onLocaleChange?.(receiveLocale)
window.addEventListener("storage", event => {
  if (!window.omna && event.key === KEY) receiveLocale(event.newValue === "zh-CN" ? "zh-CN" : "en")
})

export function setLocale(next: Locale) {
  if (next !== "en" && next !== "zh-CN") throw new Error("Unsupported language")
  // Persist before reporting success. Desktop settings are also available before React starts.
  if (window.omna?.setLocale) {
    if (!window.omna.setLocale(next)) throw new Error(t("无法保存语言设置，请重试。"))
  } else {
    try { localStorage.setItem(KEY, next) }
    catch { throw new Error(t("无法保存语言设置，请重试。")) }
  }
  locale = next
  applyLocale()
  listeners.forEach(listener => listener())
}

// Only explicit application messages go through t(). User text is rendered unchanged.
export function t(key: string, values: readonly unknown[] = []): string {
  const text = locale === "en" ? (messages[key] ?? key) : key
  return text.replace(/\{(\d+)\}/g, (token, index) => index < values.length ? String(values[Number(index)]) : token)
}

// Only this product-owned server instruction is localized. Unknown prompts stay unchanged.
export function organizePrompt(value: string): string {
  const key = "请先查一下 OMNA 里我的偏好，再读一下你的全局说明文件，把其中关于我本人的内容（身份、偏好、目标、正在做的项目）整理成记忆，逐条用 propose_memory 提交给 OMNA。只对某个代码库成立的规则、命令和路径不要提交。"
  return value === key || value === messages[key] ? t(key) : value
}
