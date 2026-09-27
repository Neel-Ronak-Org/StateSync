/**
 * What a result's columns are, read from the rows themselves (web/DESIGN.md §6).
 *
 * The server says which columns a query returned but not what they mean for a
 * chart, so the kind is inferred from the values: numbers are measures, ISO
 * dates and timestamps are time, everything else is a category. Integer
 * columns named like a part of a date ("year", "month", "hour") are treated as
 * categories: they label a bar, they are not an amount.
 */

export type ColumnKind = 'temporal' | 'quantitative' | 'nominal'

export interface Column {
  name: string
  kind: ColumnKind
  /** A readable label: "order_count" -> "Order count". */
  label: string
}

export type Row = Record<string, unknown>

const ISO_DATE = /^\d{4}-\d{2}-\d{2}(?:[T ]\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?)?$/
const DATE_PART = /^(?:year|yr|month|mon|week|wk|day|dow|weekday|hour|hr|quarter|qtr)(?:_?(?:of_?\w+|num|number))?$/i

const ACRONYMS: Record<string, string> = {
  id: 'ID', sla: 'SLA', upi: 'UPI', aov: 'AOV', gmv: 'GMV', sku: 'SKU', usd: 'USD', inr: 'INR',
  brl: 'BRL', avg: 'Avg', pct: '%', qty: 'Qty', num: 'Number of',
}

/** "total_orders" -> "Total orders", "avgSessionS" -> "Avg session s", "sla_breach_pct" -> "SLA breach %". */
export function humanize(name: string): string {
  const words = name
    .replace(/([a-z0-9])([A-Z])/g, '$1 $2')
    .split(/[\s_\-.]+/)
    .filter(Boolean)
    .map((w) => w.toLowerCase())
  if (words.length === 0) return name
  const mapped = words.map((w) => ACRONYMS[w] ?? w)
  const first = mapped[0]
  mapped[0] = first === first.toUpperCase() ? first : first.charAt(0).toUpperCase() + first.slice(1)
  return mapped.join(' ')
}

function isTemporalValue(value: unknown): boolean {
  return typeof value === 'string' && ISO_DATE.test(value.trim()) && !Number.isNaN(Date.parse(value))
}

/** The kind of one column, from its non-null values. */
export function columnKind(name: string, values: unknown[]): ColumnKind {
  const present = values.filter((v) => v !== null && v !== undefined && v !== '')
  if (present.length === 0) return 'nominal'
  if (present.every((v) => typeof v === 'number' && Number.isFinite(v))) {
    const allIntegers = present.every((v) => Number.isInteger(v))
    return allIntegers && DATE_PART.test(name) ? 'nominal' : 'quantitative'
  }
  if (present.every(isTemporalValue)) return 'temporal'
  return 'nominal'
}

/** Every column of a result, in order: the server's column list when it has one, else the first row's keys. */
export function describeColumns(rows: Row[], names?: string[] | null): Column[] {
  const order = names && names.length > 0 ? names : Object.keys(rows[0] ?? {})
  return order.map((name) => ({
    name,
    kind: columnKind(name, rows.map((r) => r[name])),
    label: humanize(name),
  }))
}
