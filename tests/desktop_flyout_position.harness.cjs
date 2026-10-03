"use strict"

// Exercises the entrypoint's actual placement function with synthetic Electron
// geometry. This proves placement math, not a native tray/display acceptance.
const assert = require("node:assert/strict")
const fs = require("node:fs")
const path = require("node:path")
const vm = require("node:vm")
const { test } = require("node:test")

const entry = fs.readFileSync(path.join(__dirname, "../apps/desktop/src/main.cjs"), "utf8")
const start = entry.indexOf("  function placeFlyout() {")
const end = entry.indexOf("  function openMain(", start)
assert.ok(start >= 0 && end > start, "actual entrypoint placement function exists")
const placement = entry.slice(start, end)

function place(display, bounds) {
  let position
  vm.runInNewContext(placement + "\nplaceFlyout()", {
    tray: { getBounds: () => bounds },
    screen: { getDisplayMatching: () => display },
    flyout: { getSize: () => [368, 520], setPosition: (x, y) => { position = [x, y] } },
  })
  return position
}

const primary = {
  bounds: { x: 0, y: 0, width: 1920, height: 1080 },
  workArea: { x: 0, y: 0, width: 1920, height: 1040 },
}

test("bottom taskbar aligns a directly visible tray icon to the lower right", () => {
  assert.deepEqual(place(primary, { x: 1850, y: 1050, width: 24, height: 24 }), [1540, 508])
})

test("bottom taskbar overflow icon inside the work area stays on the right", () => {
  assert.deepEqual(place(primary, { x: 1850, y: 1000, width: 24, height: 24 }), [1540, 508])
})

test("top taskbar still opens below its reserved strip", () => {
  const display = { ...primary, workArea: { x: 0, y: 40, width: 1920, height: 1040 } }
  assert.deepEqual(place(display, { x: 1850, y: 10, width: 24, height: 24 }), [1540, 52])
})

test("left taskbar still opens inside the left work-area edge", () => {
  const display = { ...primary, workArea: { x: 40, y: 0, width: 1880, height: 1080 } }
  assert.deepEqual(place(display, { x: 10, y: 1000, width: 24, height: 24 }), [52, 548])
})

test("right taskbar still opens inside the right work-area edge", () => {
  const display = { ...primary, workArea: { x: 0, y: 0, width: 1880, height: 1080 } }
  assert.deepEqual(place(display, { x: 1890, y: 1000, width: 24, height: 24 }), [1500, 548])
})

test("overflow icon on a monitor with a negative origin stays on that monitor's right", () => {
  const display = { bounds: { x: -1920, y: 0, width: 1920, height: 1080 }, workArea: { x: -1920, y: 0, width: 1920, height: 1040 } }
  assert.deepEqual(place(display, { x: -70, y: 980, width: 24, height: 24 }), [-380, 508])
})

test("150 percent DIP geometry does not send an overflow icon to the left", () => {
  const display = { scaleFactor: 1.5, bounds: { x: 0, y: 0, width: 1707, height: 960 }, workArea: { x: 0, y: 0, width: 1707, height: 928 } }
  assert.deepEqual(place(display, { x: 1650, y: 900, width: 16, height: 16 }), [1327, 396])
})
