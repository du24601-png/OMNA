const symbol = <>
  <path d="M 172,38 C 184,30 196,39 196,51 L 196,72 C 196,91 190,101 179,113 L 146,147 C 129,164 129,186 146,205 L 179,240 C 191,253 196,264 196,280 L 196,303 C 196,316 185,323 172,316 L 83,261 C 67,251 59,238 59,220 L 59,135 C 59,116 66,105 83,95 Z" />
  <path d="M 172,38 C 184,30 196,39 196,51 L 196,72 C 196,91 190,101 179,113 L 146,147 C 129,164 129,186 146,205 L 179,240 C 191,253 196,264 196,280 L 196,303 C 196,316 185,323 172,316 L 83,261 C 67,251 59,238 59,220 L 59,135 C 59,116 66,105 83,95 Z" transform="translate(428 0) scale(-1 1)" />
  <circle cx="214" cy="176" r="40.75" />
</>

export function BrandMark({ className = "brand-logo" }: { className?: string }) {
  return <svg className={className} viewBox="40 18 348 316" aria-hidden="true">{symbol}</svg>
}

export function BrandLockup({ className = "brand-logo" }: { className?: string }) {
  return <svg className={className} viewBox="0 0 534 124" aria-hidden="true">
    <g transform="translate(16 11) scale(.295)">{symbol}</g>
    <g transform="translate(155 27) scale(.70)" fillRule="evenodd">
      <path d="M 50,1 C 78,1 99,21 99,47 C 99,74 78,93 50,93 C 22,93 1,74 1,47 C 1,21 22,1 50,1 Z M 50,17 C 31,17 18,29 18,47 C 18,65 31,77 50,77 C 69,77 82,65 82,47 C 82,29 69,17 50,17 Z" />
      <path d="M 146,88 L 146,3 L 160,3 L 192,43 L 224,3 L 239,3 L 239,88 L 222,88 L 222,29 L 192,67 L 163,29 L 163,88 Z" />
      <path d="M 289,88 L 289,3 L 304,3 L 355,61 L 355,3 L 372,3 L 372,88 L 357,88 L 306,30 L 306,88 Z" />
      <path d="M 414,88 L 456,4 Q 460,-3 467,4 L 509,88 L 490,88 L 462,30 L 433,88 Z" />
    </g>
  </svg>
}
