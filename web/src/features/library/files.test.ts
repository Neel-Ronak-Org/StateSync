import { describe, expect, it } from 'vitest'

import { contentTypeFor, formatBytes, MAX_UPLOAD_BYTES, pagesLabel, refusalFor } from './files'

describe('contentTypeFor', () => {
  it('maps the accepted extensions, ignoring case', () => {
    expect(contentTypeFor('a.pdf')).toBe('application/pdf')
    expect(contentTypeFor('a.TXT')).toBe('text/plain')
    expect(contentTypeFor('notes.md')).toBe('text/markdown')
    expect(contentTypeFor('notes.markdown')).toBe('text/markdown')
  })
  it('refuses everything else', () => {
    expect(contentTypeFor('setup.exe')).toBeNull()
    expect(contentTypeFor('pdf')).toBeNull()
    expect(contentTypeFor('archive.pdf.zip')).toBeNull()
  })
})

describe('refusalFor', () => {
  it('names the file and the reason', () => {
    expect(refusalFor({ name: 'x.exe', size: 10 })).toBe('x.exe: only PDF, text and Markdown files are supported.')
    expect(refusalFor({ name: 'big.pdf', size: MAX_UPLOAD_BYTES + 1 })).toBe('big.pdf is larger than 20 MB.')
  })
  it('accepts a file at exactly the limit', () => {
    expect(refusalFor({ name: 'ok.pdf', size: MAX_UPLOAD_BYTES })).toBeNull()
  })
})

describe('formatBytes and pagesLabel', () => {
  it('uses KB below 1 MB and MB above', () => {
    expect(formatBytes(2048)).toBe('2 KB')
    expect(formatBytes(1536 * 1024)).toBe('1.5 MB')
  })
  it('says pages only when there are some', () => {
    expect(pagesLabel(null)).toBeNull()
    expect(pagesLabel(1)).toBe('1 page')
    expect(pagesLabel(12)).toBe('12 pages')
  })
})
