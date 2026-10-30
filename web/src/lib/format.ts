/**
 * Formatting helpers for numbers and relative times.
 */

const numberFormatter = new Intl.NumberFormat('en-US')

export function formatNumber(val: number): string {
  return numberFormatter.format(val)
}

// A column named like a year ("year", "fiscal_year", "period_end_year", "fy") holds years, not quantities.
const YEAR_COLUMN = /(^|_)(year|yr|fy)(_|$)/i

/** A result cell's number: years plain (2018, not "2,018"), everything else with thousands separators. */
export function formatCellNumber(column: string, val: number): string {
  return YEAR_COLUMN.test(column) && Number.isInteger(val) ? String(val) : formatNumber(val)
}

export function formatRelativeTime(dateInput: string | number | Date): { relative: string; absolute: string } {
  const date = typeof dateInput === 'string' || typeof dateInput === 'number' ? new Date(dateInput) : dateInput
  // Checked before toISOString(), which throws on an invalid date.
  if (isNaN(date.getTime())) {
    return { relative: '', absolute: '' }
  }
  const absolute = date.toISOString()
  const diffMs = Date.now() - date.getTime()
  const diffSec = Math.max(0, Math.floor(diffMs / 1000))
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffSec < 10) {
    return { relative: 'just now', absolute }
  }
  if (diffSec < 60) {
    return { relative: `${diffSec} s ago`, absolute }
  }
  if (diffMin < 60) {
    return { relative: `${diffMin} min ago`, absolute }
  }
  if (diffHour < 24) {
    return { relative: `${diffHour} h ago`, absolute }
  }
  if (diffDay === 1) {
    return { relative: 'yesterday', absolute }
  }
  if (diffDay < 7) {
    return { relative: `${diffDay} d ago`, absolute }
  }
  return { relative: date.toLocaleDateString(), absolute }
}

export function formatInterval(seconds: number): string {
  if (seconds < 60) {
    return `${Math.round(seconds)} s`
  }
  if (seconds < 3600) {
    const mins = Math.round(seconds / 60)
    return `${mins} min`
  }
  if (seconds < 86400) {
    const hours = Math.round(seconds / 3600)
    return `${hours} h`
  }
  const days = Math.round(seconds / 86400)
  return `${days} d`
}

