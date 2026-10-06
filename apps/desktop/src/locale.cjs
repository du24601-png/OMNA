"use strict"

const fs = require("node:fs")

// Kept separate from application data so the UI preference is available before startup.
function createLocaleStore(file) {
  let locale = "en"
  try {
    const saved = JSON.parse(fs.readFileSync(file, "utf8"))
    if (saved.locale === "zh-CN") locale = "zh-CN"
  } catch { /* Missing or invalid preferences use English. */ }
  return {
    get: () => locale,
    set(next) {
      if (next !== "en" && next !== "zh-CN") return false
      const temp = file + ".tmp"
      try {
        fs.writeFileSync(temp, JSON.stringify({ locale: next }), { encoding: "utf8", mode: 0o600 })
        fs.renameSync(temp, file)
        locale = next
        return true
      } catch {
        try { fs.unlinkSync(temp) } catch { /* No temporary file to remove. */ }
        return false
      }
    },
    text: (zh, en) => locale === "zh-CN" ? zh : en,
  }
}

module.exports = { createLocaleStore }
