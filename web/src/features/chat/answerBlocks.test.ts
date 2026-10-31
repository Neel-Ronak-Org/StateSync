import { describe, expect, it } from 'vitest'
import { answerBlocks } from './answerBlocks'

describe('answerBlocks', () => {
  it('handles plain paragraphs', () => {
    const text = 'First paragraph.\n\nSecond paragraph.'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'First paragraph.' },
      { kind: 'p', text: 'Second paragraph.' },
    ])
  })

  it('handles a "- " list after an intro line', () => {
    const text = 'Here are the options:\n- Option A\n- Option B'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'Here are the options:' },
      { kind: 'ul', items: ['Option A', 'Option B'] },
    ])
  })

  it('handles a numbered list', () => {
    const text = 'Steps:\n1. First step\n2. Second step\n3. Third step'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'Steps:' },
      { kind: 'ol', items: ['First step', 'Second step', 'Third step'] },
    ])
  })

  it('keeps list items separated by blank lines in one list, but not across a paragraph', () => {
    const text = 'Risks:\n\n1. Supply\n\n2. Labour\n\n3. Legal\n\nIn short, many.\n\n- A\n\n- B'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'Risks:' },
      { kind: 'ol', items: ['Supply', 'Labour', 'Legal'] },
      { kind: 'p', text: 'In short, many.' },
      { kind: 'ul', items: ['A', 'B'] },
    ])
  })

  it('handles the inline " - " case after a colon', () => {
    const text = 'According to the table: - Fashion: 5 days - Electronics: 7 days'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'According to the table:' },
      { kind: 'ul', items: ['Fashion: 5 days', 'Electronics: 7 days'] },
    ])
  })

  it('handles a paragraph with a single inline " - " (must stay one <p>)', () => {
    const text = 'According to the table: - Fashion: 5 days'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'According to the table: - Fashion: 5 days' },
    ])
  })

  it('handles text with no list at all', () => {
    const text = 'This is a single paragraph without any lists.'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'This is a single paragraph without any lists.' },
    ])
  })

  it('handles bullet lists starting with "* "', () => {
    const text = '* Star item 1\n* Star item 2'
    expect(answerBlocks(text)).toEqual([
      { kind: 'ul', items: ['Star item 1', 'Star item 2'] },
    ])
  })

  it('handles trailing lines after a list in the same paragraph', () => {
    const text = 'Intro:\n- Item 1\n- Item 2\nOutro text.'
    expect(answerBlocks(text)).toEqual([
      { kind: 'p', text: 'Intro:' },
      { kind: 'ul', items: ['Item 1', 'Item 2'] },
      { kind: 'p', text: 'Outro text.' },
    ])
  })
})
