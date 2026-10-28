export type AnswerBlock =
  | { kind: 'p'; text: string }
  | { kind: 'ul' | 'ol'; items: string[] }

export function answerBlocks(text: string): AnswerBlock[] {
  if (!text || !text.trim()) {
    return []
  }

  const normalized = text.replace(/\r\n/g, '\n').trim()
  const paragraphs = normalized.split(/\n\n+/)
  const result: AnswerBlock[] = []

  for (const rawParagraph of paragraphs) {
    const paragraph = rawParagraph.trim()
    if (!paragraph) continue

    // Inline " - " list items after the first colon on ONE line
    // e.g. "According to the table: - Fashion: 5 days - Electronics: 7 days"
    const colonIdx = paragraph.indexOf(':')
    if (colonIdx !== -1 && !paragraph.includes('\n')) {
      const intro = paragraph.slice(0, colonIdx + 1).trim()
      const afterColon = paragraph.slice(colonIdx + 1).trim()
      if (afterColon.startsWith('- ')) {
        const rawItems = afterColon.slice(2).split(/\s+-\s+/)
        const items = rawItems.map((s) => s.trim()).filter(Boolean)
        if (items.length >= 2) {
          result.push({ kind: 'p', text: intro })
          result.push({ kind: 'ul', items })
          continue
        }
      }
    }

    // Line-by-line parsing inside the paragraph
    const lines = paragraph.split('\n')
    let currentKind: 'p' | 'ul' | 'ol' | null = null
    let pLines: string[] = []
    let listItems: string[] = []

    const flush = () => {
      if (currentKind === 'p' && pLines.length > 0) {
        result.push({ kind: 'p', text: pLines.join('\n') })
        pLines = []
      } else if ((currentKind === 'ul' || currentKind === 'ol') && listItems.length > 0) {
        // Models often leave a blank line between list items; items split that way stay one list
        // (otherwise every numbered item would read "1.").
        const last = result[result.length - 1]
        if (last && last.kind === currentKind) {
          last.items.push(...listItems)
        } else {
          result.push({ kind: currentKind, items: listItems })
        }
        listItems = []
      }
      currentKind = null
    }

    for (const line of lines) {
      const trimmed = line.trim()
      if (!trimmed) continue

      const bulletMatch = line.match(/^(\s*[-*]\s+)(.*)$/)
      const numberedMatch = line.match(/^(\s*\d+\.\s+)(.*)$/)

      if (bulletMatch) {
        if (currentKind !== 'ul') {
          flush()
          currentKind = 'ul'
        }
        listItems.push(bulletMatch[2].trim())
      } else if (numberedMatch) {
        if (currentKind !== 'ol') {
          flush()
          currentKind = 'ol'
        }
        listItems.push(numberedMatch[2].trim())
      } else {
        if (currentKind !== 'p') {
          flush()
          currentKind = 'p'
        }
        pLines.push(trimmed)
      }
    }

    flush()
  }

  return result
}
