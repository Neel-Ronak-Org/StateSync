// Pure logic for the Pipeline Inspector: turns an answer's trace into the steps the page shows.
// Nothing here fetches or renders; every function is tested in inspect.test.ts.

import type { FrontDoorTrace, InspectPassage, ModelCall, RetrievalTrace, SchemaGraph, StateTrace } from '@/api/types'

export type StepId = 'router' | 'search' | 'rerank' | 'sql' | 'writer'
export type PathRoute = 'sql' | 'docs' | 'hybrid' | 'neither'

export function frontDoor(trace: StateTrace | null | undefined): FrontDoorTrace | null {
  const fd = trace?.front_door
  return fd && typeof fd === 'object' ? (fd as FrontDoorTrace) : null
}

export function retrievalTrace(trace: StateTrace | null | undefined): RetrievalTrace | null {
  const rt = trace?.retrieval_trace
  return rt && typeof rt === 'object' && Array.isArray((rt as RetrievalTrace).passages) ? (rt as RetrievalTrace) : null
}

/** The route the answer took, as the flow diagram draws it. */
export function pathRoute(trace: StateTrace | null | undefined): PathRoute {
  const route = trace?.route as string | null | undefined
  if (route === 'sql' || route === 'docs' || route === 'hybrid') return route
  return 'neither'
}

/** Which steps of the flow ran for this route, in order. */
export function stepsFor(route: PathRoute, hasReranking: boolean): StepId[] {
  if (route === 'neither') return ['router', 'writer']
  if (route === 'sql') return ['router', 'sql', 'writer']
  const docs: StepId[] = hasReranking ? ['search', 'rerank'] : ['search']
  return route === 'docs' ? ['router', ...docs, 'writer'] : ['router', 'sql', ...docs, 'writer']
}

/** Rank (from 1) of each passage by one score, highest first; ties keep list order. Passages without the score get null. */
export function ranksBy(passages: InspectPassage[], key: 'score' | 'rerank_score' | 'jev_score'): Map<string, number | null> {
  const scored = passages
    .map((p, i) => ({ id: p.chunk_id, value: p[key], i }))
    .filter((x): x is { id: string; value: number; i: number } => typeof x.value === 'number')
    .sort((a, b) => b.value - a.value || a.i - b.i)
  const out = new Map<string, number | null>(passages.map((p) => [p.chunk_id, null]))
  scored.forEach((x, rank) => out.set(x.id, rank + 1))
  return out
}

export interface RankRow {
  passage: InspectPassage
  dense: number | null
  reranker: number | null
  jev: number | null
  final: number
  /** Among the passages the answer writer reads. */
  read: boolean
  /** Places gained (positive) between the search's order and the writer's order. */
  moved: number
}

/** One row per candidate, in the writer's order, with its rank at every stage. */
export function rankRows(rt: RetrievalTrace): RankRow[] {
  const dense = ranksBy(rt.passages, 'score')
  const reranker = ranksBy(rt.passages, 'rerank_score')
  const jev = ranksBy(rt.passages, 'jev_score')
  return rt.passages.map((p, i) => {
    const final = p.rank_final ?? i + 1
    return {
      passage: p,
      dense: dense.get(p.chunk_id) ?? null,
      reranker: reranker.get(p.chunk_id) ?? null,
      jev: jev.get(p.chunk_id) ?? null,
      final,
      read: final <= rt.settings.window,
      moved: p.rank_searched ? p.rank_searched - final : 0,
    }
  })
}

/** How many passages Jev moved into, and out of, the writer's window. */
export function windowChanges(rows: RankRow[], window: number): { movedIn: number; movedOut: number } {
  let movedIn = 0
  let movedOut = 0
  for (const r of rows) {
    const before = r.passage.rank_searched
    if (!before) continue
    if (before > window && r.final <= window) movedIn += 1
    if (before <= window && r.final > window) movedOut += 1
  }
  return { movedIn, movedOut }
}

/** The documents the first round picked, in order of their best passage, with how many passages each had. */
export function documentsOf(passages: InspectPassage[]): { document_id: string; title: string; passages: number; best: number }[] {
  const seen = new Map<string, { document_id: string; title: string; passages: number; best: number }>()
  for (const p of passages) {
    const cur = seen.get(p.document_id)
    if (cur) {
      cur.passages += 1
      cur.best = Math.max(cur.best, p.score)
    } else {
      seen.set(p.document_id, { document_id: p.document_id, title: p.title || p.document_id, passages: 1, best: p.score })
    }
  }
  return [...seen.values()]
}

/** Jev's probabilities for the routing options, highest first, with the chosen one marked. */
export function routeProbabilities(fd: FrontDoorTrace | null): { option: string; label: string; p: number; chosen: boolean }[] {
  const probs = fd?.jev?.probabilities
  if (!probs || typeof probs !== 'object') return []
  const labels: Record<string, string> = {
    sql: 'Database only', docs: 'Documents only', both: 'Both sources', hybrid: 'Both sources', neither: 'Neither source',
    needs_database: 'Needs the database', needs_documents: 'Needs the documents',
  }
  const entries = Object.entries(probs).filter(([, v]) => typeof v === 'number')
  const top = entries.reduce((best, [, v]) => Math.max(best, v), -1)
  return entries
    .map(([option, p]) => ({ option, label: labels[option] ?? option, p, chosen: p === top }))
    .sort((a, b) => b.p - a.p)
}

/** The tables a SQL statement reads, among those the schema has (FROM and JOIN targets; schema prefix and quotes ignored). */
export function tablesUsed(sql: string | null | undefined, graph: SchemaGraph | null | undefined): string[] {
  if (!sql || !graph) return []
  const known = new Map(graph.tables.map((t) => [t.name.toLowerCase(), t.name]))
  const found = new Set<string>()
  const re = /\b(?:from|join)\s+((?:"[^"]+"|[\w$]+)(?:\s*\.\s*(?:"[^"]+"|[\w$]+))?)/gi
  let m: RegExpExecArray | null
  while ((m = re.exec(sql)) !== null) {
    const last = m[1].split('.').pop() ?? ''
    const name = known.get(last.trim().replace(/^"|"$/g, '').toLowerCase())
    if (name) found.add(name)
  }
  return graph.tables.map((t) => t.name).filter((n) => found.has(n))
}

export interface CostRow {
  step: string
  detail: string
  calls: number
  tokensIn: number
  tokensOut: number
  ms: number
}

const ROLE_LABELS: Record<string, string> = {
  supervisor: 'Router (LLM)', front_door: 'Router (LLM)', normalizer: 'Language check', rewriter: 'Follow-up rewrite',
  sql: 'SQL writer', synthesizer: 'Answer writer', decomposer: 'Question split', judge: 'Judge',
}

/** Every model step of the answer in order: the LLM calls, plus Jev's routing and passage scoring (which are not LLM calls). */
export function costRows(trace: StateTrace | null | undefined): CostRow[] {
  const rows: CostRow[] = []
  const fd = frontDoor(trace)
  if (fd?.jev) {
    rows.push({ step: 'Router (Jev)', detail: String(fd.jev.model ?? 'jev'), calls: 1, tokensIn: 0, tokensOut: 0, ms: Number(fd.jev.ms ?? 0) })
  }
  for (const c of (trace?.calls ?? []) as ModelCall[]) {
    rows.push({ step: ROLE_LABELS[c.role] ?? c.role, detail: c.model, calls: 1, tokensIn: c.tokens_in ?? 0, tokensOut: c.tokens_out ?? 0, ms: c.ms })
  }
  const rt = retrievalTrace(trace)
  if (rt) {
    rows.push({ step: 'Documents search', detail: rt.settings.rerank && rt.settings.rerank !== 'off' ? `dense + ${rt.settings.rerank} re-ranker` : 'dense', calls: 2, tokensIn: 0, tokensOut: 0, ms: rt.ms.search })
    if (rt.jev) {
      rows.push({ step: 'Passage scoring (Jev)', detail: `${rt.jev.scored} scored, ${rt.jev.failed} failed`, calls: rt.jev.scored + rt.jev.failed, tokensIn: 0, tokensOut: 0, ms: rt.ms.jev })
    }
  }
  return rows
}

export function formatMs(ms: number | null | undefined): string {
  if (ms == null || !Number.isFinite(ms)) return '—'
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`
}

/** A passage's label: its document title (or id) and page. */
export function passageLabel(p: Pick<InspectPassage, 'title' | 'document_id' | 'page'>): string {
  const title = p.title || p.document_id
  return p.page == null ? title : `${title}, p. ${p.page}`
}

/** Lays tables out in columns of roughly equal height: returns each table's column and its y offset, plus the canvas size. */
export function layoutTables(
  graph: SchemaGraph, columns: number, tableWidth: number, rowHeight: number, headerHeight: number, gap: number,
): { positions: Map<string, { x: number; y: number; height: number }>; width: number; height: number } {
  const cols = Math.max(1, columns)
  const heights = new Array<number>(cols).fill(0)
  const positions = new Map<string, { x: number; y: number; height: number }>()
  for (const t of graph.tables) {
    const h = headerHeight + t.columns.length * rowHeight
    let col = 0
    for (let i = 1; i < cols; i += 1) if (heights[i] < heights[col]) col = i
    positions.set(t.name, { x: col * (tableWidth + gap), y: heights[col], height: h })
    heights[col] += h + gap
  }
  return { positions, width: cols * tableWidth + (cols - 1) * gap, height: Math.max(0, ...heights) - gap }
}
