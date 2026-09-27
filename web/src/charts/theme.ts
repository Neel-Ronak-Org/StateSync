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
  '--chart-surface': '#ffffff',
  '--chart-grid': '#e5e7eb',
  '--chart-domain': '#d1d5db',
  '--chart-label': '#6b7280',
  '--chart-title': '#111827',
  '--chart-c1': '#4f46e5',
  '--chart-c2': '#0891b2',
  '--chart-c3': '#16a34a',
  '--chart-c4': '#d97706',
  '--chart-c5': '#db2777',
  '--chart-c6': '#7c3aed',
  '--chart-c7': '#475569',
  '--chart-c8': '#dc2626',
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
    bar: { fill: primary, cornerRadiusEnd: 3 },
    line: { stroke: primary, strokeWidth: 2 },
    point: { fill: primary, filled: true, size: 48 },
    area: { fill: primary, fillOpacity: 0.18, line: { stroke: primary, strokeWidth: 2 } },
    arc: { stroke: surface, strokeWidth: 1 },
    tick: { color: primary, thickness: 2 },
    text: { color: label, font: FONT, fontSize: 11 },
    style: {
      'guide-label': { fill: label, font: FONT, fontSize: 11 },
      'guide-title': { fill: label, font: FONT, fontSize: 11 },
      'group-title': { fill: title, font: FONT },
    },
  }
}
