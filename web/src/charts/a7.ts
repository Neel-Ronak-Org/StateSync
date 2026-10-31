/**
 * The chart allowlist ("A7", the name of the answer's output contract), check 2 of 2.
 *
 * The orchestrator checks every chart spec before it leaves the server
 * (orchestrator/schemas/synthesizer_output.py, check 1). This one runs again in the browser, independently, before
 * anything reaches Vega, so either validator alone blocks a hostile spec.
 * There is no repair path: a spec that fails is withheld, and the prose answer
 * stays.
 *
 * What it blocks and why:
 * - any property outside the allowlist, at the top level or in an encoding
 *   channel (object-form marks are where mark properties smuggle expressions);
 * - data other than inline `values` (no url, name or format: no network fetch);
 * - expression-bearing keys anywhere in transforms: Vega expressions
 *   are code, and even the CSP-safe interpreter should not be handed them;
 * - out-of-range sizes and oversized data.
 */

export const A7 = {
  MARKS: ['bar', 'line', 'point', 'area', 'tick', 'rect', 'arc'],
  ENCODING_PROPS: ['field', 'type', 'aggregate', 'title', 'sort'],
  ENCODING_TYPES: ['quantitative', 'nominal', 'ordinal', 'temporal'],
  AGGREGATES: ['sum', 'mean', 'count', 'min', 'max', 'median'],
  TOP_LEVEL: ['$schema', 'mark', 'data', 'encoding', 'transform', 'title', 'width', 'height'],
  REQUIRED: ['mark', 'encoding', 'data'],
  SCHEMA_URL: 'https://vega.github.io/schema/vega-lite/v5.json',
  /** Always expression strings, wherever they appear. */
  EXPRESSION_KEYS: ['expr', 'signal', 'calculate', 'test', 'init', 'update', 'on'],
  /** `filter` takes an expression string OR a non-executable predicate object. */
  DUAL_FORM_EXPRESSION_KEYS: ['filter'],
  MAX_VALUES: 5000,
  MAX_TRANSFORMS: 8,
  MIN_DIM: 100,
  MAX_WIDTH: 1600,
  MAX_HEIGHT: 1200,
} as const

type Json = Record<string, unknown>

function isPlainObject(v: unknown): v is Json {
  return v !== null && typeof v === 'object' && !Array.isArray(v)
}

const includes = (list: readonly string[], value: unknown): boolean =>
  typeof value === 'string' && list.includes(value)

function auditExpressions(node: unknown, path: string, out: string[]): void {
  if (Array.isArray(node)) {
    node.forEach((item, i) => auditExpressions(item, `${path}/${i}`, out))
  } else if (isPlainObject(node)) {
    for (const [k, v] of Object.entries(node)) {
      if (includes(A7.EXPRESSION_KEYS, k)) {
        out.push(`${path}/${k}: expression-bearing property is not permitted`)
      } else if (includes(A7.DUAL_FORM_EXPRESSION_KEYS, k) && typeof v === 'string') {
        out.push(`${path}/${k}: string form is an expression; use the predicate-object form instead`)
      }
      auditExpressions(v, `${path}/${k}`, out)
    }
  }
}

/** Every violation found; an empty list means the spec may be rendered. `null` is valid (no chart). */
export function validateA7Chart(chart: unknown): string[] {
  const errs: string[] = []
  if (chart === null || chart === undefined) return errs
  if (!isPlainObject(chart)) {
    errs.push('chart: must be an object or null')
    return errs
  }

  for (const key of A7.REQUIRED) {
    if (!(key in chart)) errs.push(`chart: missing required property '${key}'`)
  }
  for (const key of Object.keys(chart)) {
    if (!includes(A7.TOP_LEVEL, key)) errs.push(`chart/${key}: unknown top-level property`)
  }

  if ('$schema' in chart && chart.$schema !== A7.SCHEMA_URL) {
    errs.push(`chart/$schema: must be exactly ${A7.SCHEMA_URL}`)
  }

  if ('mark' in chart) {
    if (typeof chart.mark !== 'string') errs.push('chart/mark: must be a string enum value, not an object')
    else if (!includes(A7.MARKS, chart.mark)) errs.push(`chart/mark: '${chart.mark}' is not in the permitted enum`)
  }

  if ('data' in chart) {
    const data = chart.data
    if (!isPlainObject(data)) errs.push('chart/data: must be an object')
    else {
      if (!('values' in data)) errs.push("chart/data: missing required property 'values'")
      for (const key of Object.keys(data)) {
        if (key !== 'values') errs.push(`chart/data/${key}: unknown property (only 'values' is permitted)`)
      }
      if ('values' in data) {
        if (!Array.isArray(data.values)) errs.push('chart/data/values: must be an array')
        else if (data.values.length > A7.MAX_VALUES) {
          errs.push(`chart/data/values: ${data.values.length} items exceeds maximum of ${A7.MAX_VALUES}`)
        }
      }
    }
  }

  if ('encoding' in chart) {
    const encoding = chart.encoding
    if (!isPlainObject(encoding)) errs.push('chart/encoding: must be an object')
    else {
      for (const [channel, def] of Object.entries(encoding)) {
        if (!isPlainObject(def)) {
          errs.push(`chart/encoding/${channel}: must be an object`)
          continue
        }
        for (const key of Object.keys(def)) {
          if (!includes(A7.ENCODING_PROPS, key)) errs.push(`chart/encoding/${channel}/${key}: unknown encoding property`)
        }
        if ('type' in def && !includes(A7.ENCODING_TYPES, def.type)) {
          errs.push(`chart/encoding/${channel}/type: '${String(def.type)}' is not a permitted type`)
        }
        if ('aggregate' in def && !includes(A7.AGGREGATES, def.aggregate)) {
          errs.push(`chart/encoding/${channel}/aggregate: '${String(def.aggregate)}' is not a permitted aggregate`)
        }
        if ('field' in def && typeof def.field !== 'string') {
          errs.push(`chart/encoding/${channel}/field: must be a string`)
        }
      }
    }
  }

  if ('transform' in chart) {
    if (!Array.isArray(chart.transform)) errs.push('chart/transform: must be an array')
    else if (chart.transform.length > A7.MAX_TRANSFORMS) {
      errs.push(`chart/transform: ${chart.transform.length} items exceeds maximum of ${A7.MAX_TRANSFORMS}`)
    }
    auditExpressions(chart.transform, 'chart/transform', errs)
  }

  if ('title' in chart && typeof chart.title !== 'string') errs.push('chart/title: must be a string')
  for (const [dim, max] of [['width', A7.MAX_WIDTH], ['height', A7.MAX_HEIGHT]] as const) {
    if (dim in chart) {
      const v = chart[dim]
      if (typeof v !== 'number' || !Number.isInteger(v)) errs.push(`chart/${dim}: must be an integer`)
      else if (v < A7.MIN_DIM || v > max) errs.push(`chart/${dim}: ${v} outside [${A7.MIN_DIM}, ${max}]`)
    }
  }

  return errs
}


// A year field ("year", "fiscal_year", "period_end_year") on a number axis: the theme's compact "~s" format
// turns 2023 into "2.023k", so such an axis gets a plain integer format instead.
const YEAR_FIELD = /(^|_)(year|yr|fy)(_|$)/i

/** A copy of the spec whose x/y encodings on year fields use a plain integer axis format (unless one is set).
 * Applied after validateA7Chart (which refuses axis properties from a spec): the format is a fixed value of ours. */
export function withYearAxes(spec: Record<string, unknown>): Record<string, unknown> {
  const encoding = spec.encoding
  if (!encoding || typeof encoding !== 'object') return spec
  const enc = encoding as Record<string, unknown>
  let changed = false
  const next: Record<string, unknown> = { ...enc }
  for (const channel of ['x', 'y']) {
    const ch = enc[channel]
    if (!ch || typeof ch !== 'object') continue
    const c = ch as Record<string, unknown>
    if (c.type !== 'quantitative' || typeof c.field !== 'string' || !YEAR_FIELD.test(c.field)) continue
    const axis = c.axis && typeof c.axis === 'object' ? (c.axis as Record<string, unknown>) : {}
    if ('format' in axis || c.axis === null) continue
    next[channel] = { ...c, axis: { ...axis, format: 'd' } }
    changed = true
  }
  return changed ? { ...spec, encoding: next } : spec
}
