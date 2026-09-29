/** Which files the library takes, decided in the browser before anything is sent. */

export const MAX_UPLOAD_BYTES = 20 * 1024 * 1024
export const ACCEPT = '.pdf,.txt,.md,.markdown'

const TYPE_BY_EXTENSION: Record<string, string> = {
  pdf: 'application/pdf',
  txt: 'text/plain',
  md: 'text/markdown',
  markdown: 'text/markdown',
}

/** The upload Content-Type, by file extension; null when the library does not take the file. */
export function contentTypeFor(filename: string): string | null {
  const dot = filename.lastIndexOf('.')
  if (dot < 0) return null
  return TYPE_BY_EXTENSION[filename.slice(dot + 1).toLowerCase()] ?? null
}

/** Why a file is refused in the browser, or null when it may be sent. */
export function refusalFor(file: { name: string; size: number }): string | null {
  if (contentTypeFor(file.name) === null) return `${file.name}: only PDF, text and Markdown files are supported.`
  if (file.size > MAX_UPLOAD_BYTES) return `${file.name} is larger than 20 MB.`
  return null
}

const sizeFormatter = new Intl.NumberFormat('en-US', { maximumFractionDigits: 1 })

export function formatBytes(bytes: number): string {
  if (bytes < 1024 * 1024) return `${sizeFormatter.format(Math.max(1, Math.round(bytes / 1024)))} KB`
  return `${sizeFormatter.format(bytes / (1024 * 1024))} MB`
}

export function pagesLabel(pages: number | null): string | null {
  if (pages === null) return null
  return `${pages} ${pages === 1 ? 'page' : 'pages'}`
}

/** The sentence a folder delete confirmation shows. */
export function folderDeleteWarning(folder: { document_count: number; workspace_ids: string[] }): string[] {
  const n = folder.document_count
  const lines = [`This deletes the folder and its ${n} ${n === 1 ? 'document' : 'documents'}. It cannot be undone.`]
  const used = folder.workspace_ids.length
  if (used > 0) lines.push(`Used by ${used} ${used === 1 ? 'workspace' : 'workspaces'}.`)
  return lines
}
