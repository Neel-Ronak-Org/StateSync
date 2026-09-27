/**
 * Vega config built from the app's CSS tokens (index.css: --chart-*).
 *
 * A compiled Vega view captures its config by value, so a theme switch means
 * re-embedding with a fresh config; VegaChart does that. Every guide is
 * coloured explicitly: leaving axis and legend text to Vega's near-black
 * defaults is what made charts illegible on a dark background before.
 *
 * Tooltips stay OFF: vega-tooltip builds its table with innerHTML from raw
 * row values, an untrusted-data sink. The results table shows the values.
 */

const FALLBACK: Record<string, string> = {
  '--chart-surface': '#121b2d',
  '--chart-grid': '#1e2a40',
  '--chart-domain': '#2a3852',
  '--chart-label': '#8b97ad',
  '--chart-title': '#e6edf7',
  '--chart-c1': '#5cc8e8',
  '--chart-c2': '#8b7cf6',
  '--chart-c3': '#2dd4bf',
  '--chart-c4': '#fbbf24',
  '--chart-c5': '#f472b6',
  '--chart-c6': '#a3e635',
  '--chart-c7': '#38bdf8',
  '--chart-c8': '#fb923c',
}

/** A `#rrggbb` colour with alpha, for gradient stops. Anything else is returned as is. */
export function withAlpha(hex: string, alpha: number): string {
  const m = /^#([0-9a-f]{2})([0-9a-f]{2})([0-9a-f]{2})$/i.exec(hex.trim())
  if (!m) return hex
  const [r, g, b] = m.slice(1).map((h) => parseInt(h, 16))
  return `rgba(${r}, ${g}, ${b}, ${alpha})`
}

const FONT = "'Geist Variable', ui-sans-serif, system-ui, sans-serif"

function token(styles: CSSStyleDeclaration, name: string): string {
  return styles.getPropertyValue(name).trim() || FALLBACK[name] || 'gray'
}

export function chartColor(name: string): string {
  return token(getComputedStyle(document.documentElement), name)
}

export function vegaConfig(): Record<string, unknown> {
  const cs = getComputedStyle(document.documentElement)
  const surface = token(cs, '--chart-surface')
  const grid = token(cs, '--chart-grid')
  const domain = token(cs, '--chart-domain')
  const label = token(cs, '--chart-label')
  const title = token(cs, '--chart-title')
  const categorical = Array.from({ length: 8 }, (_, i) => token(cs, `--chart-c${i + 1}`))
  const primary = categorical[0]

  return {
    background: surface,
    padding: { left: 4, right: 12, top: 8, bottom: 4 },
    autosize: { type: 'fit', contains: 'padding', resize: true },
    font: FONT,
    view: { stroke: null, continuousWidth: 520, continuousHeight: 260 },
    axis: {
      domain: true,
      domainColor: domain,
      ticks: false,
      grid: true,
      gridColor: grid,
      labelColor: label,
      labelFont: FONT,
      labelFontSize: 11,
      labelPadding: 6,
      labelOverlap: 'greedy',
      labelSeparation: 8,
      labelLimit: 160,
      titleColor: label,
      titleFont: FONT,
      titleFontSize: 11,
      titleFontWeight: 500,
      titlePadding: 8,
      tickCount: 6,
    },
    // Horizontal labels; Vega-Lite otherwise turns short category names
    // vertical. labelOverlap 'greedy' drops colliding labels instead.
    axisX: { grid: false, labelAngle: 0 },
    axisY: { domain: false, format: '~s' },
    legend: {
      orient: 'top',
      direction: 'horizontal',
      symbolType: 'circle',
      symbolSize: 64,
      labelColor: label,
      labelFont: FONT,
      labelFontSize: 11,
      titleColor: label,
      titleFont: FONT,
      titleFontSize: 11,
      titleFontWeight: 500,
      columnPadding: 12,
    },
    title: { color: title, font: FONT, fontSize: 13, fontWeight: 600, anchor: 'start', offset: 10 },
    range: { category: categorical },
    mark: { tooltip: false },
    bar: { fill: primary, cornerRadiusEnd: 4 },
    line: { stroke: primary, strokeWidth: 2.5, interpolate: 'monotone' },
    point: { fill: primary, filled: true, size: 48 },
    // The reference's filled trend: the series colour fading to nothing.
    area: {
      interpolate: 'monotone',
      fill: {
        gradient: 'linear',
        x1: 0, y1: 1, x2: 0, y2: 0,
        stops: [
          { offset: 0, color: withAlpha(primary, 0) },
          { offset: 1, color: withAlpha(primary, 0.35) },
        ],
      },
      line: { stroke: primary, strokeWidth: 2.5 },
    },
    // A donut: the hole is config, because A7 keeps the mark a plain string.
    arc: { innerRadius: 48, padAngle: 0.012, cornerRadius: 3, stroke: surface, strokeWidth: 2 },
    rect: { cornerRadius: 2 },
    tick: { color: primary, thickness: 2 },
    text: { color: label, font: FONT, fontSize: 11 },
    style: {
      'guide-label': { fill: label, font: FONT, fontSize: 11 },
      'guide-title': { fill: label, font: FONT, fontSize: 11 },
      'group-title': { fill: title, font: FONT },
    },
  }
}
