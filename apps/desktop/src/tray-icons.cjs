"use strict"

// Draws the tray icon for each state into a premultiplied BGRA bitmap, so no
// image files ship with the app and every DPI gets crisp pixels. The mark is
// the brand symbol (brand/OMNA_Symbol_Black.svg: two mirrored shapes around
// one central dot), fitted to a 24-unit grid. States follow
// docs/v2/design/source/Tray.dc.html: the central dot turns green while an
// agent reads, a green badge marks pending suggestions, the dot is left out
// while sharing is paused, and a faded mark with an amber warning means the
// service has a problem.

const GREEN = { light: [0x2e, 0x76, 0x56], dark: [0x6c, 0xc4, 0x9b] }
const AMBER = [0xc2, 0x86, 0x1a]
const SAMPLES = 4

// Left side of the symbol in its own coordinates; the right side mirrors it
// around x = 214. Curves are flattened once into a polygon.
const SIDE_PATH = "M 172,38 C 184,30 196,39 196,51 L 196,72 C 196,91 190,101 179,113 L 146,147 C 129,164 129,186 146,205 L 179,240 C 191,253 196,264 196,280 L 196,303 C 196,316 185,323 172,316 L 83,261 C 67,251 59,238 59,220 L 59,135 C 59,116 66,105 83,95 Z"
const SIDE = flatten(SIDE_PATH)
// viewBox 40 18 348 316, fitted 22 units wide and centred on the grid.
const UNIT = 22 / 348
const LEFT = 1 - 40 * UNIT
const TOP = (24 - 316 * UNIT) / 2 - 18 * UNIT
const DOT = { x: LEFT + 214 * UNIT, y: TOP + 176 * UNIT, r: 40.75 * UNIT }

function flatten(path) {
  const numbers = path.match(/-?\d+(\.\d+)?/g).map(Number)
  const commands = path.match(/[MCLZ]/g)
  const points = []
  let i = 0
  for (const command of commands) {
    if (command === "M" || command === "L") { points.push([numbers[i], numbers[i + 1]]); i += 2 }
    if (command === "C") {
      const [x0, y0] = points[points.length - 1]
      const [x1, y1, x2, y2, x3, y3] = numbers.slice(i, i + 6)
      for (let step = 1; step <= 12; step += 1) {
        const t = step / 12, u = 1 - t
        points.push([
          u * u * u * x0 + 3 * u * u * t * x1 + 3 * u * t * t * x2 + t * t * t * x3,
          u * u * u * y0 + 3 * u * u * t * y1 + 3 * u * t * t * y2 + t * t * t * y3,
        ])
      }
      i += 6
    }
  }
  return points
}

function inside(polygon, x, y) {
  let hit = false
  for (let a = 0, b = polygon.length - 1; a < polygon.length; b = a, a += 1) {
    const [xa, ya] = polygon[a], [xb, yb] = polygon[b]
    if ((ya > y) !== (yb > y) && x < ((xb - xa) * (y - ya)) / (yb - ya) + xa) hit = !hit
  }
  return hit
}

function sides(x, y) {
  const sx = (x - LEFT) / UNIT
  const sy = (y - TOP) / UNIT
  return inside(SIDE, sx, sy) || inside(SIDE, 428 - sx, sy)
}

function disc(x, y, cx, cy, radius) {
  return Math.hypot(x - cx, y - cy) <= radius
}

function dot(x, y) {
  return disc(x, y, DOT.x, DOT.y, DOT.r)
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
  const ink = theme === "dark" ? [0xff, 0xff, 0xff] : [0x17, 0x17, 0x17]
  const green = GREEN[theme]
  const base = [[sides, ink, 1], [dot, ink, 1]]
  if (state === "reading") {
    const glow = 0.3 + 0.7 * phase
    return [[sides, ink, 1], [(x, y) => disc(x, y, DOT.x, DOT.y, DOT.r * 1.12), green, glow]]
  }
  if (state === "pending") {
    return [...base, [(x, y) => disc(x, y, 19.6, 4.4, 4.4), null, 1], [(x, y) => disc(x, y, 19.6, 4.4, 3.0), green, 1]]
  }
  if (state === "paused") {
    // The central dot (the person) is left out: nothing is shared right now.
    return [[sides, ink, 1]]
  }
  if (state === "problem") {
    return [
      [sides, ink, 0.55],
      [dot, ink, 0.55],
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
