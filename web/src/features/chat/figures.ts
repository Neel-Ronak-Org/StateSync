/**
 * Figures in an answer's prose and where each came from (orchestrator/number_grounding.py).
 *
 * The server returns each figure's text in reading order. The answer is split
 * into paragraphs and list items before it is drawn, so offsets into the raw
 * text do not survive; instead each chunk is searched for the next figures in
 * order, consuming them, which keeps two identical numbers in different
 * places tied to their own sources.
 */

import type { GroundedFigure } from '@/api/types'

export interface Segment {
  text: string
  figure?: GroundedFigure
}

/** A cursor over the figures, shared by the chunks of one answer. */
export function figureCursor(figures: GroundedFigure[] | undefined | null) {
  let next = 0
  const list = figures ?? []
  return function segments(chunk: string): Segment[] {
    const out: Segment[] = []
    let rest = chunk
    while (next < list.length) {
      const figure = list[next]
      const at = rest.indexOf(figure.text)
      if (at < 0) break
      if (at > 0) out.push({ text: rest.slice(0, at) })
      out.push({ text: figure.text, figure })
      rest = rest.slice(at + figure.text.length)
      next += 1
    }
    if (rest) out.push({ text: rest })
    return out
  }
}

/** What a reader sees on hover: where the figure came from, in words. */
export function figureSource(figure: GroundedFigure): string {
  const s = figure.source
  if (!figure.matched || !s) return 'Not found in the result. Check it against the table.'
  if (s.kind === 'cell') return `From the result: row ${(s.row ?? 0) + 1}, ${s.column}`
  if (s.kind === 'aggregate') {
    if (s.aggregate === 'count') return 'The number of rows in the result'
    const word = { sum: 'Total', avg: 'Average', min: 'Smallest', max: 'Largest' }[s.aggregate ?? ''] ?? 'Computed'
    return `${word} of ${s.column} in the result`
  }
  if (s.kind === 'text') return `Part of a value in the result: row ${(s.row ?? 0) + 1}, ${s.column}`
  if (s.kind === 'derived') return 'Worked out from other figures in this answer'
  if (s.kind === 'question') return 'From your question'
  return 'From a cited passage'
}
