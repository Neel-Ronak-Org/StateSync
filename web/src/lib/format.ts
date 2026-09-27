/**
 * Formatting helpers for numbers and relative times.
 */

const numberFormatter = new Intl.NumberFormat('en-US')

export function formatNumber(val: number): string {
  return numberFormatter.format(val)
}

export function formatRelativeTime(dateInput: string | number | Date): { relative: string; absolute: string } {
  const date = typeof dateInput === 'string' || typeof dateInput === 'number' ? new Date(dateInput) : dateInput
  const absolute = date.toISOString()
  if (isNaN(date.getTime())) {
    return { relative: '', absolute: '' }
  }
  const diffMs = Date.now() - date.getTime()
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffSec < 60) {
    return { relative: 'just now', absolute }
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
