import { describe, expect, it } from 'vitest'

import type { GroundedFigure } from '@/api/types'
import { figureCursor, figureSource } from '@/features/chat/figures'

const cell = (text: string, row: number, column: string): GroundedFigure => ({
  text, start: 0, end: 0, matched: true, source: { kind: 'cell', row, column },
})
const unmatched = (text: string): GroundedFigure => ({ text, start: 0, end: 0, matched: false, source: null })

describe('figureCursor', () => {
  it('splits chunks around figures in order and carries the cursor across chunks', () => {
    const next = figureCursor([cell('134', 0, 'n'), cell('134', 1, 'n'), unmatched('12%')])
    expect(next('Mumbai had 134 orders.')).toEqual([
      { text: 'Mumbai had ' }, { text: '134', figure: cell('134', 0, 'n') }, { text: ' orders.' }])
    const second = next('Delhi also had 134, up 12%.')
    expect(second.filter((s) => s.figure).map((s) => s.figure)).toEqual([cell('134', 1, 'n'), unmatched('12%')])
    expect(second.map((s) => s.text).join('')).toBe('Delhi also had 134, up 12%.')
  })

  it('leaves text untouched without figures', () => {
    expect(figureCursor(null)('No numbers here.')).toEqual([{ text: 'No numbers here.' }])
  })
})

describe('figureSource', () => {
  it('says where each figure came from', () => {
    expect(figureSource(cell('134', 2, 'n'))).toBe('From the result: row 3, n')
    expect(figureSource({ ...cell('267', 0, 'n'), source: { kind: 'aggregate', aggregate: 'sum', column: 'n' } }))
      .toBe('Total of n in the result')
    expect(figureSource({ ...cell('2', 0, 'n'), source: { kind: 'aggregate', aggregate: 'count', column: 'rows' } }))
      .toBe('The number of rows in the result')
    expect(figureSource({ ...cell('5', 0, 'n'), source: { kind: 'question' } })).toBe('From your question')
    expect(figureSource(unmatched('12%'))).toMatch(/^Not found in the result/)
  })
})
