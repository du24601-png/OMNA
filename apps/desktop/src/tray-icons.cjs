"use strict"

// Draws the tray icon for each state into a premultiplied BGRA bitmap, so no
// image files ship with the app and every DPI gets crisp pixels. Shapes follow
// docs/v2/design/source/Tray.dc.html on a 24-unit grid: a ring with a dot,
// a green dot while an agent reads, a green badge for pending suggestions,
// a dashed ring with pause bars, and a faded ring with an amber warning.

const GREEN = { light: [0x2e, 0x76, 0x56], dark: [0x6c, 0xc4, 0x9b] }
const AMBER = [0xc2, 0x86, 0x1a]
const SAMPLES = 4

function ring(x, y, radius, width) {
  return Math.abs(Math.hypot(x - 12, y - 12) - radius) <= width / 2
}

function dashedRing(x, y) {
  if (!ring(x, y, 8.5, 2.2)) return false
  const angle = (Math.atan2(y - 12, x - 12) + Math.PI * 2.5) % (Math.PI * 2)
  return (angle * 8.5) % 5.8 < 3.2
}

function disc(x, y, cx, cy, radius) {
  return Math.hypot(x - cx, y - cy) <= radius
}

function capsule(x, y, x0, y0, y1, width) {
  const cy = Math.min(Math.max(y, y0), y1)
  return Math.hypot(x - x0, y - cy) <= width / 2
}

function triangle(x, y) {
  // Warning badge in the lower right: apex (18.5, 12.5), base y = 23.
  if (y > 23 || y < 12.5) return false
  const half = (y - 12.5) * 0.62
  return Math.abs(x - 18.5) <= half
}

// Each layer: [test(x, y), rgb, alpha]. Later layers paint over earlier ones;
// a layer with rgb === null clears what is under it (the badge cut-out).
function layers(state, theme, phase) {
  const ink = theme === "dark" ? [0xff, 0xff, 0xff] : [0x1b, 0x1b, 0x1b]
  const green = GREEN[theme]
  const base = [[(x, y) => ring(x, y, 8.5, 2.2), ink, 1], [(x, y) => disc(x, y, 12, 12, 3.2), ink, 1]]
  if (state === "reading") {
    const glow = 0.3 + 0.7 * phase
    return [[(x, y) => ring(x, y, 8.5, 2.2), ink, 1], [(x, y) => disc(x, y, 12, 12, 3.6), green, glow]]
  }
  if (state === "pending") {
    return [...base, [(x, y) => disc(x, y, 19.2, 4.8, 4.6), null, 1], [(x, y) => disc(x, y, 19.2, 4.8, 3.2), green, 1]]
  }
  if (state === "paused") {
    return [
      [dashedRing, ink, 1],
      [(x, y) => capsule(x, y, 10.2, 9.4, 14.6, 1.9), ink, 1],
      [(x, y) => capsule(x, y, 13.8, 9.4, 14.6, 1.9), ink, 1],
    ]
  }
  if (state === "problem") {
    return [
      [(x, y) => ring(x, y, 8.5, 2.2), ink, 0.55],
      [(x, y) => disc(x, y, 12, 12, 3.2), ink, 0.55],
      [triangleOutline, null, 1],
      [triangle, AMBER, 1],
      [(x, y) => capsule(x, y, 18.5, 16.2, 19.4, 1.5), [0xff, 0xff, 0xff], 1],
      [(x, y) => disc(x, y, 18.5, 21.4, 0.85), [0xff, 0xff, 0xff], 1],
    ]
  }
  return base
}

function triangleOutline(x, y) {
  if (y > 24.2 || y < 10.6) return false
  return Math.abs(x - 18.5) <= (y - 10.6) * 0.62 + 0.4
}

function render(state, theme, size, phase = 1) {
  const paint = layers(state, theme, phase)
  const buffer = Buffer.alloc(size * size * 4)
  const scale = 24 / size
  for (let py = 0; py < size; py += 1) {
    for (let px = 0; px < size; px += 1) {
      let r = 0, g = 0, b = 0, a = 0
      for (let sy = 0; sy < SAMPLES; sy += 1) {
        for (let sx = 0; sx < SAMPLES; sx += 1) {
          const x = (px + (sx + 0.5) / SAMPLES) * scale
          const y = (py + (sy + 0.5) / SAMPLES) * scale
          let cr = 0, cg = 0, cb = 0, ca = 0
          for (const [test, rgb, alpha] of paint) {
            if (!test(x, y)) continue
            if (rgb === null) { cr = cg = cb = ca = 0; continue }
            cr = rgb[0] * alpha + cr * (1 - alpha)
            cg = rgb[1] * alpha + cg * (1 - alpha)
            cb = rgb[2] * alpha + cb * (1 - alpha)
            ca = alpha + ca * (1 - alpha)
          }
          r += cr; g += cg; b += cb; a += ca
        }
      }
      const n = SAMPLES * SAMPLES
      const offset = (py * size + px) * 4
      // premultiplied BGRA, what nativeImage.createFromBitmap expects on Windows
      buffer[offset] = Math.round(b / n)
      buffer[offset + 1] = Math.round(g / n)
      buffer[offset + 2] = Math.round(r / n)
      buffer[offset + 3] = Math.round((a / n) * 255)
    }
  }
  return buffer
}

module.exports = { render, STATES: ["idle", "reading", "pending", "paused", "problem"] }
