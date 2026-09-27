/**
 * Formatting helpers for numbers and relative times.
 */

const numberFormatter = new Intl.NumberFormat('en-US')

export function formatNumber(val: number): string {
  return numberFormatter.format(val)
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

