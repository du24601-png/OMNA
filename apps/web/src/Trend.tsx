import { useReducedMotion } from "motion/react"
import { useEffect, useState } from "react"
import { CartesianGrid, Line, LineChart, ResponsiveContainer, Tooltip, XAxis, YAxis } from "recharts"
import { api, type AccessReads } from "./api"
import { useResource } from "./ui"

const LIGHT = ["#1d4ed8", "#15803d", "#6d28d9", "#0f766e", "#9a3412"]
const DARK = ["#64b5ff", "#6ee78a", "#d7a4f7", "#5eead4", "#ffb088"]

function useChartTheme() {
  const [theme, setTheme] = useState({ accent: "#171717", dark: false })
  useEffect(() => {
    const read = () => {
      const style = getComputedStyle(document.documentElement)
      setTheme({
        accent: style.getPropertyValue("--accent").trim() || "#171717",
        dark: document.documentElement.dataset.mode === "dark",
      })
    }
    read()
    const observer = new MutationObserver(read)
    observer.observe(document.documentElement, { attributes: true, attributeFilter: ["data-mode", "data-accent"] })
    return () => observer.disconnect()
  }, [])
  return theme
}

function axisDay(iso: string) {
  const parts = iso.split("-")
  return `${Number(parts[1])}/${Number(parts[2])}`
}

function fullDay(iso: string) {
  const parts = iso.split("-")
  return `${Number(parts[1])}月${Number(parts[2])}日`
}

type TrendRange = "24h" | 7 | 14

function axisHour(iso: string) {
  return iso.slice(11, 16)
}

function fullHour(iso: string) {
  const [date, clock] = iso.split("T")
  const parts = date.split("-")
  return `${Number(parts[1])}月${Number(parts[2])}日 ${clock.slice(0, 5)}`
}

function chartView(data: AccessReads, range: TrendRange) {
  if (range === "24h") {
    const recent = data.last_24h ?? { hours: [], series: [] }
    return {
      buckets: recent.hours,
      series: recent.series.filter(item => item.counts.some(count => count > 0)),
      hourly: true,
    }
  }
  const start = Math.max(0, data.days.length - range)
  return {
    buckets: data.days.slice(start),
    series: data.series
      .map(item => ({ ...item, counts: item.counts.slice(start) }))
      .filter(item => item.counts.some(count => count > 0)),
    hourly: false,
  }
}

type TipItem = { name?: string; value?: number | string; color?: string; payload?: { full?: string } }

function TrendTip({ active, payload }: { active?: boolean; payload?: TipItem[] }) {
  if (!active || !payload?.length) return null
  return <div className="trend-tip">
    {payload[0]?.payload?.full && <div className="trend-tip-date">{payload[0].payload.full}</div>}
    {payload.map(item => <div key={item.name} className="trend-tip-row"><i style={{ background: item.color }} /><span>{item.name}</span><strong>{item.value}</strong></div>)}
  </div>
}

export function ReadTrend({ tick }: { tick: number }) {
  const resource = useResource(() => api.accessReads(), [tick])
  const [range, setRange] = useState<TrendRange>(7)
  const [hidden, setHidden] = useState<string[]>([])
  const reduce = useReducedMotion()
  const theme = useChartTheme()
  const view = resource.data ? chartView(resource.data, range) : null
  const rest = theme.dark ? DARK : LIGHT
  const colorOf = (index: number) => index === 0 ? theme.accent : rest[(index - 1) % rest.length]
  const hiddenSet = new Set(hidden)
  const rows = view?.buckets.map((bucket, index) => {
    const row: Record<string, string | number> = {
      label: view.hourly ? axisHour(bucket) : axisDay(bucket),
      full: view.hourly ? fullHour(bucket) : fullDay(bucket),
    }
    view.series.forEach(item => { if (!hiddenSet.has(item.id)) row[item.id] = item.counts[index] ?? 0 })
    return row
  }) ?? []
  const summary = view?.series.map(item => `${item.name} ${item.counts.reduce((sum, count) => sum + count, 0)} 次`).join("，")
  const rangeLabel = range === "24h" ? "24 小时" : `${range} 天`
  return <section className="side-pane trend-pane" aria-labelledby="read-trend-title">
    <header className="pane-head">
      <div>
        <h2 id="read-trend-title">记忆调用</h2>
      </div>
      <div className="trend-range" role="group" aria-label="时间范围">
        <button type="button" aria-pressed={range === "24h"} onClick={() => setRange("24h")}>24h</button>
        {([7, 14] as const).map(days => <button key={days} type="button" aria-pressed={range === days} onClick={() => setRange(days)}>{days} 天</button>)}
      </div>
    </header>
    {resource.error && <div className="notice error" role="alert"><div><strong>加载未完成</strong><p>{resource.error}</p></div><button className="button secondary" onClick={resource.reload}>重试加载</button></div>}
    {resource.loading && !resource.data && !resource.error && <div className="trend-skeleton" role="status" aria-label="正在加载记忆调用" />}
    {view && !view.series.length && !resource.error && <p className="trend-empty">这 {rangeLabel}还没有 Agent 读取记忆</p>}
    {view && !!view.series.length && <>
      <p className="sr-only">{`最近 ${rangeLabel}，${summary}`}</p>
      <div className="trend-plot">
        <ResponsiveContainer width="100%" height="100%">
          <LineChart data={rows} margin={{ top: 12, right: 16, left: 0, bottom: 0 }}>
            <CartesianGrid vertical={false} stroke="var(--border)" />
            <XAxis dataKey="label" tickLine={false} axisLine={false} tickMargin={8} interval={range === "24h" ? 3 : range === 14 ? 1 : 0} tick={{ fill: "var(--muted)", fontSize: 11 }} />
            <YAxis allowDecimals={false} width={28} tickLine={false} axisLine={false} tick={{ fill: "var(--muted)", fontSize: 11 }} />
            <Tooltip content={<TrendTip />} cursor={{ stroke: "var(--border)", strokeWidth: 1 }} />
            {view.series.filter(item => !hiddenSet.has(item.id)).map(item => {
              const color = colorOf(view.series.findIndex(series => series.id === item.id))
              return <Line key={item.id} type="monotone" dataKey={item.id} name={item.name} stroke={color} strokeWidth={1.75} dot={false} activeDot={{ r: 3.5, strokeWidth: 0, fill: color }} isAnimationActive={!reduce} />
            })}
          </LineChart>
        </ResponsiveContainer>
      </div>
      <div className="trend-legend">
        {view.series.map((item, index) => {
          const off = hiddenSet.has(item.id)
          return <button key={item.id} type="button" className={off ? "off" : ""} aria-pressed={!off} onClick={() => setHidden(current => current.includes(item.id) ? current.filter(id => id !== item.id) : [...current, item.id])}><i style={{ background: colorOf(index) }} />{item.name}</button>
        })}
      </div>
    </>}
  </section>
}
