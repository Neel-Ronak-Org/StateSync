/**
 * RFC 4180 CSV export with formula injection guard.
 */

function formatCsvValue(val: unknown): string {
  if (val === null || val === undefined) return ''
  if (typeof val === 'number' || typeof val === 'boolean') {
    return String(val)
  }
  let str = String(val)
  // CSV formula-injection guard: strings starting with =, +, -, @ get a leading apostrophe.
  if (/^[=+\-@]/.test(str)) {
    str = `'${str}`
  }
  // RFC 4180: quote if string contains quote, comma, or newline.
  if (str.includes('"') || str.includes(',') || str.includes('\n') || str.includes('\r')) {
    return `"${str.replace(/"/g, '""')}"`
  }
  return str
}

export function downloadCsv(columns: string[], rows: Record<string, unknown>[], filename: string): void {
  const header = columns.map(formatCsvValue).join(',')
  const lines = [header]
  for (const row of rows) {
    const line = columns.map((col) => formatCsvValue(row[col])).join(',')
    lines.push(line)
  }
  const csvContent = lines.join('\n')
  const blob = new Blob([csvContent], { type: 'text/csv;charset=utf-8;' })
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename.endsWith('.csv') ? filename : `${filename}.csv`
  document.body.appendChild(a)
  a.click()
  document.body.removeChild(a)
  URL.revokeObjectURL(url)
}
