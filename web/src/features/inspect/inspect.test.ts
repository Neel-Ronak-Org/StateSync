import { describe, expect, it } from 'vitest'

import type { InspectPassage, RetrievalTrace, SchemaGraph, StateTrace } from '@/api/types'
import {
  costRows, documentsOf, formatMs, layoutTables, passageLabel, pathRoute, rankRows, ranksBy, routeProbabilities, stepsFor,
  tablesUsed, windowChanges,
} from '@/features/inspect/inspect'

const p = (id: string, score: number, jev: number | null, searched: number, final: number, doc = 'd1'): InspectPassage => ({
  chunk_id: id, document_id: doc, title: doc === 'd1' ? 'ACME 2022 10K' : 'ACME 2021 10K', page: 3, score,
  jev_score: jev, rank_searched: searched, rank_final: final,
})

const rt: RetrievalTrace = {
  search_text: 'q', settings: { window: 2, candidates: 25 }, round1: [], narrowed_to: [],
  passages: [p('c', 0.6, 0.95, 3, 1), p('a', 0.7, 0.6, 1, 2, 'd2'), p('b', 0.8, 0.1, 2, 3)],
  jev: { scored: 3, failed: 0, ms: 40 }, ms: { search: 120, jev: 40 },
}

describe('routes and steps', () => {
  it('maps the trace route, with anything else as neither', () => {
    expect(pathRoute({ route: 'docs' } as StateTrace)).toBe('docs')
    expect(pathRoute({ route: null } as StateTrace)).toBe('neither')
    expect(pathRoute(null)).toBe('neither')
  })
  it('lists the steps of each route', () => {
    expect(stepsFor('sql', false)).toEqual(['router', 'sql', 'writer'])
    expect(stepsFor('docs', true)).toEqual(['router', 'search', 'rerank', 'writer'])
    expect(stepsFor('hybrid', false)).toEqual(['router', 'sql', 'search', 'writer'])
    expect(stepsFor('neither', true)).toEqual(['router', 'writer'])
  })
})

describe('ranks', () => {
  it('ranks by a score, highest first, null without one', () => {
    const ranks = ranksBy([...rt.passages, { ...p('z', 0.1, null, 4, 4) }], 'jev_score')
    expect([ranks.get('c'), ranks.get('a'), ranks.get('b'), ranks.get('z')]).toEqual([1, 2, 3, null])
  })
  it('gives each candidate its rank at every stage and what the writer reads', () => {
    const rows = rankRows(rt)
    expect(rows.map((r) => [r.passage.chunk_id, r.dense, r.jev, r.final, r.read, r.moved])).toEqual([
      ['c', 3, 1, 1, true, 2], ['a', 2, 2, 2, true, -1], ['b', 1, 3, 3, false, -1],
    ])
    expect(windowChanges(rows, 2)).toEqual({ movedIn: 1, movedOut: 1 })
  })
  it('groups passages by document in first-seen order', () => {
    expect(documentsOf(rt.passages)).toEqual([
      { document_id: 'd1', title: 'ACME 2022 10K', passages: 2, best: 0.8 },
      { document_id: 'd2', title: 'ACME 2021 10K', passages: 1, best: 0.7 },
    ])
  })
})

describe('router probabilities', () => {
  it('sorts the options and marks the highest', () => {
    const rows = routeProbabilities({ arm: 'jev_choice', route: 'docs', jev: { probabilities: { sql: 0.1, docs: 0.7, both: 0.15, neither: 0.05 } } })
    expect(rows.map((r) => [r.option, r.chosen])).toEqual([['docs', true], ['both', false], ['sql', false], ['neither', false]])
    expect(rows[0].label).toBe('Documents only')
  })
  it('is empty without Jev', () => {
    expect(routeProbabilities({ arm: 'llm', route: 'sql' })).toEqual([])
    expect(routeProbabilities(null)).toEqual([])
  })
})

const graph: SchemaGraph = {
  dataset: 'sec', tables_total: 3, edges: [],
  tables: [
    { name: 'companies', row_estimate: 31, columns: [{ name: 'ticker', type: 'text', nullable: false, foreign_key: false }] },
    { name: 'annual_metrics', row_estimate: 5000, columns: [
      { name: 'ticker', type: 'text', nullable: false, foreign_key: true }, { name: 'value', type: 'numeric', nullable: true, foreign_key: false }] },
    { name: 'facts', row_estimate: 9, columns: [] },
  ],
}

describe('schema', () => {
  it('finds the tables a statement reads, with schema prefixes and quotes', () => {
    const sql = 'SELECT c.name FROM sec.annual_metrics am JOIN "companies" c ON c.ticker = am.ticker WHERE x IN (SELECT 1 FROM other_table)'
    expect(tablesUsed(sql, graph)).toEqual(['companies', 'annual_metrics'])
    expect(tablesUsed(null, graph)).toEqual([])
    expect(tablesUsed(sql, null)).toEqual([])
  })
  it('lays tables out in balanced columns', () => {
    const { positions, width, height } = layoutTables(graph, 2, 200, 20, 30, 10)
    expect(positions.get('companies')).toEqual({ x: 0, y: 0, height: 50 })
    expect(positions.get('annual_metrics')).toEqual({ x: 210, y: 0, height: 70 })
    expect(positions.get('facts')).toEqual({ x: 0, y: 60, height: 30 })
    expect(width).toBe(410)
    expect(height).toBe(90)
  })
})

describe('cost rows and labels', () => {
  it('lists Jev routing, the model calls, the search and the passage scoring', () => {
    const trace = {
      route: 'docs', front_door: { arm: 'jev_choice', route: 'docs', jev: { model: 'jev-1.13', ms: 900 } },
      calls: [{ role: 'synthesizer', model: 'gpt-oss-120b', tokens_in: 4000, tokens_out: 300, ms: 2500 }],
      retrieval_trace: rt,
    } as unknown as StateTrace
    expect(costRows(trace).map((r) => [r.step, r.calls, r.ms])).toEqual([
      ['Router (Jev)', 1, 900], ['Answer writer', 1, 2500], ['Documents search', 2, 120], ['Passage scoring (Jev)', 3, 40],
    ])
    expect(costRows(null)).toEqual([])
  })
  it('formats times and passage labels', () => {
    expect([formatMs(450), formatMs(2231), formatMs(null)]).toEqual(['450 ms', '2.2 s', '—'])
    expect(passageLabel({ title: null, document_id: 'up_1', page: null })).toBe('up_1')
    expect(passageLabel({ title: 'ACME 2022 10K', document_id: 'up_1', page: 7 })).toBe('ACME 2022 10K, p. 7')
  })
})
