import { Fragment, useMemo, useRef, useState, type ReactNode } from 'react'
import { useQuery } from '@tanstack/react-query'
import { ArrowDown, ArrowUp, Check, ChevronDown, ChevronRight, ShieldCheck } from 'lucide-react'

import { api } from '@/api/client'
import type { Retrieval, StateTrace } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import {
  costRows, documentsOf, formatMs, frontDoor, passageLabel, pathRoute, rankRows, retrievalTrace, routeProbabilities,
  stepsFor, tablesUsed, windowChanges, type StepId,
} from '@/features/inspect/inspect'
import { PipelineFlow } from '@/features/inspect/PipelineFlow'
import { SchemaDiagram } from '@/features/inspect/SchemaDiagram'
import { formatNumber } from '@/lib/format'
import { cn } from '@/lib/utils'
import type { ChatTurn } from '@/types/chat'

interface InspectorDialogProps {
  turn: ChatTurn | null
  open: boolean
  onOpenChange: (open: boolean) => void
}

function StepCard({
  id, number, title, time, children, anchor,
}: {
  id: StepId | 'cost'
  number: number
  title: string
  time?: number
  children: ReactNode
  anchor: (id: string) => (el: HTMLElement | null) => void
}) {
  return (
    <section ref={anchor(id)} data-testid={`inspect-step-${id}`} className="scroll-mt-4 rounded-xl border bg-background">
      <header className="flex items-center gap-3 border-b px-4 py-2.5">
        <span className="flex size-6 shrink-0 items-center justify-center rounded-full bg-primary text-xs font-semibold text-primary-foreground">
          {number}
        </span>
        <h3 className="flex-1 text-sm font-semibold text-foreground">{title}</h3>
        {time != null && time > 0 && <span className="font-mono text-xs text-muted-foreground">{formatMs(time)}</span>}
      </header>
      <div className="space-y-4 p-4 text-sm">{children}</div>
    </section>
  )
}

function Label({ children }: { children: ReactNode }) {
  return <h4 className="mb-1.5 text-xs font-medium uppercase tracking-wide text-muted-foreground">{children}</h4>
}

function Sql({ sql }: { sql: string }) {
  return <pre className="max-h-56 overflow-auto whitespace-pre-wrap rounded-md bg-muted p-3 font-mono text-xs leading-relaxed">{sql}</pre>
}

function retrievalText(retrievals: Retrieval[]): Map<string, string> {
  const out = new Map<string, string>()
  for (const r of retrievals) {
    const prov = (r as { provenance?: { chunk_id?: string } }).provenance
    const id = prov?.chunk_id ?? r.chunk_id
    if (id && typeof r.content === 'string') out.set(id, r.content)
  }
  return out
}

function callsMs(trace: StateTrace, roles: string[]): number {
  return (trace.calls ?? []).filter((c) => roles.includes(c.role)).reduce((sum, c) => sum + c.ms, 0)
}

/** How one answer was produced, step by step, from the trace the server returned with it. */
export function InspectorDialog({ turn, open, onOpenChange }: InspectorDialogProps) {
  const anchors = useRef(new Map<string, HTMLElement>())
  const [expanded, setExpanded] = useState<string | null>(null)
  const trace = turn?.stateTrace ?? null
  const workspaceId = (trace?.workspace_id as string | null | undefined) ?? null
  const route = pathRoute(trace)
  const usesDatabase = route === 'sql' || route === 'hybrid'

  const schema = useQuery({
    queryKey: ['schema-graph', workspaceId],
    queryFn: () => api.workspaceSchemaGraph(workspaceId!),
    enabled: open && usesDatabase && Boolean(workspaceId),
    staleTime: 5 * 60 * 1000,
  })

  const fd = frontDoor(trace)
  const rt = retrievalTrace(trace)
  const rows = useMemo(() => (rt ? rankRows(rt) : []), [rt])
  const texts = useMemo(() => retrievalText(trace?.retrievals ?? []), [trace])

  if (!turn || !trace) return null

  const hasJevOrder = Boolean(rt?.jev && rt.jev.scored + rt.jev.failed > 0)
  const steps = stepsFor(route, Boolean(rt) && hasJevOrder)
  const rerankLabel = hasJevOrder ? 'Jev' : 'none'
  const probabilities = routeProbabilities(fd)
  const routerLabel = fd?.jev && !fd.jev.fallback ? 'one Jev choice' : fd ? 'LLM router' : 'LLM router'
  const times: Partial<Record<StepId, number>> = {
    router: fd?.latency_ms ?? (fd?.jev?.ms as number | undefined) ?? callsMs(trace, ['supervisor']),
    sql: callsMs(trace, ['sql']),
    search: rt?.ms.search,
    rerank: rt?.ms.jev,
    writer: callsMs(trace, ['synthesizer']),
  }
  const anchor = (id: string) => (el: HTMLElement | null) => {
    if (el) anchors.current.set(id, el)
    else anchors.current.delete(id)
  }
  const jump = (id: StepId) => anchors.current.get(id)?.scrollIntoView({ behavior: 'smooth', block: 'start' })

  const window = rt?.settings.window ?? 8
  const changes = windowChanges(rows, window)
  const used = tablesUsed(trace.executed_sql, schema.data)
  const errors = (trace.errors ?? []) as { attempt?: number; category?: string; message?: string; offending_sql?: string | null }[]
  const samples = (trace.interrogation_samples ?? []) as { table?: string; columns_sampled?: string[]; rows?: unknown[] }[]
  const hybrid = (trace.hybrid_entities ?? null) as { entities?: string[]; matched_documents?: string[]; narrowed?: boolean } | null
  const grounding = trace.number_grounding ?? null
  const cost = costRows(trace)
  const readPassages = rows.filter((r) => r.read)
  const fallbackPassages = (trace.retrievals ?? []).slice(0, window)
  let n = 0

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent
        data-testid="inspector"
        className="flex h-[92vh] w-[96vw] max-w-[96vw] flex-col gap-0 overflow-hidden p-0 sm:max-w-[min(1240px,96vw)]"
      >
        <DialogHeader className="border-b px-5 py-3">
          <DialogTitle className="text-base">Pipeline Inspector</DialogTitle>
          <DialogDescription className="truncate text-xs" title={turn.question}>
            How this answer was produced: “{turn.question}” · {formatMs(turn.latencyMs)} in total
          </DialogDescription>
        </DialogHeader>

        <div className="flex-1 space-y-4 overflow-y-auto bg-muted/20 p-5">
          <PipelineFlow route={route} steps={steps} times={times} rerankLabel={rerankLabel} routerLabel={routerLabel} onSelect={jump} />

          {/* ── Router ─────────────────────────────────────────────────────── */}
          <StepCard id="router" number={(n += 1)} title="Router: which source should answer?" time={times.router} anchor={anchor}>
            {!fd ? (
              <p className="text-muted-foreground">
                This server routed with its standard router and recorded only the result:{' '}
                <span className="font-medium text-foreground">{route}</span>.
              </p>
            ) : (
              <>
                <div className="grid gap-4 md:grid-cols-2">
                  <div>
                    <Label>Decision</Label>
                    {probabilities.length > 0 ? (
                      <div data-testid="route-probabilities" className="space-y-1.5">
                        {probabilities.map((p) => (
                          <div key={p.option} className="flex items-center gap-2 text-xs">
                            <span className={cn('w-32 shrink-0', p.chosen ? 'font-semibold text-foreground' : 'text-muted-foreground')}>{p.label}</span>
                            <div className="h-3 flex-1 overflow-hidden rounded bg-muted">
                              <div className={cn('h-full rounded', p.chosen ? 'bg-primary' : 'bg-muted-foreground/40')} style={{ width: `${Math.round(p.p * 100)}%` }} />
                            </div>
                            <span className="w-10 shrink-0 text-right font-mono">{p.p.toFixed(2)}</span>
                          </div>
                        ))}
                        <p className="pt-1 text-xs text-muted-foreground">
                          One multiple-choice question to Jev ({String(fd.jev?.model ?? 'jev')}); no LLM call. The highest probability is the route.
                        </p>
                      </div>
                    ) : (
                      <p className="text-xs text-muted-foreground">
                        {fd.jev?.fallback
                          ? `Jev could not be reached (${String(fd.jev.jev_error ?? 'error')}), so the LLM router decided: `
                          : 'The LLM router decided: '}
                        <span className="font-medium text-foreground">{fd.route}</span>
                      </p>
                    )}
                    {fd.database_question && fd.documents_question && (
                      <div className="mt-3 space-y-1 text-xs">
                        <Label>The question was split</Label>
                        <p><span className="text-muted-foreground">Database part:</span> {fd.database_question}</p>
                        <p><span className="text-muted-foreground">Documents part:</span> {fd.documents_question}</p>
                      </div>
                    )}
                    {fd.standalone_question && fd.standalone_question !== turn.question && (
                      <p className="mt-3 text-xs"><span className="text-muted-foreground">Read as:</span> {fd.standalone_question}</p>
                    )}
                  </div>
                  <div>
                    <Label>Best-matching documents it was shown</Label>
                    {fd.documents && fd.documents.length > 0 ? (
                      <ul className="space-y-1 text-xs">
                        {fd.documents.map((d, i) => (
                          <li key={i} className="flex items-center justify-between gap-2 rounded border px-2 py-1">
                            <span className="truncate" title={d.title}>{d.title}</span>
                            <span className="shrink-0 font-mono text-muted-foreground">{d.score.toFixed(3)}</span>
                          </li>
                        ))}
                      </ul>
                    ) : (
                      <p className="text-xs text-muted-foreground">No documents matched.</p>
                    )}
                  </div>
                </div>
                {fd.card && (
                  <details className="rounded-md border">
                    <summary className="cursor-pointer px-3 py-2 text-xs font-medium">
                      Database card it was shown ({formatNumber(fd.card.length)} characters: tables, columns and what the rows cover)
                    </summary>
                    <pre data-testid="database-card" className="max-h-64 overflow-auto whitespace-pre-wrap border-t bg-muted p-3 font-mono text-[11px] leading-relaxed">{fd.card}</pre>
                  </details>
                )}
              </>
            )}
          </StepCard>

          {/* ── Database ───────────────────────────────────────────────────── */}
          {steps.includes('sql') && (
            <StepCard id="sql" number={(n += 1)} title="Database: write, check and run the query" time={times.sql} anchor={anchor}>
              <div>
                <Label>Where the query sits in the database</Label>
                {!workspaceId ? (
                  <p className="text-xs text-muted-foreground">Ask inside a workspace to see its database here.</p>
                ) : schema.isLoading ? (
                  <Skeleton className="h-40 w-full" />
                ) : schema.data ? (
                  <>
                    <SchemaDiagram graph={schema.data} highlighted={used} />
                    <p className="mt-1.5 text-xs text-muted-foreground">
                      {used.length > 0 ? `Highlighted: the ${used.length === 1 ? 'table' : 'tables'} this query read (${used.join(', ')}).` : 'No table of this database appears in the query.'}
                    </p>
                  </>
                ) : (
                  <p className="text-xs text-muted-foreground">The database's shape could not be loaded.</p>
                )}
              </div>
              {samples.length > 0 && (
                <div>
                  <Label>Rows the model looked at before writing</Label>
                  <ul className="space-y-1 text-xs">
                    {samples.map((s, i) => (
                      <li key={i} className="rounded border px-2 py-1">
                        <span className="font-mono">{s.table}</span>
                        <span className="text-muted-foreground"> · {(s.rows ?? []).length} sample rows · columns {(s.columns_sampled ?? []).join(', ')}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              {errors.length > 0 && (
                <div>
                  <Label>Attempts that failed, and why</Label>
                  <ul className="space-y-2">
                    {errors.map((e, i) => (
                      <li key={i} className="rounded-md border border-destructive/40 p-2 text-xs">
                        <p className="font-medium text-destructive">Attempt {e.attempt}: {e.category}</p>
                        <p className="mt-0.5 text-muted-foreground">{e.message}</p>
                        {e.offending_sql && <div className="mt-1.5"><Sql sql={e.offending_sql} /></div>}
                      </li>
                    ))}
                  </ul>
                </div>
              )}
              <div>
                <Label>{errors.length > 0 ? `Query that ran (attempt ${trace.attempt + 1})` : 'Query that ran'}</Label>
                {trace.executed_sql ? <Sql sql={trace.executed_sql} /> : <p className="text-xs text-muted-foreground">No query reached the database.</p>}
                {trace.explanation && <p className="mt-2 text-xs"><span className="text-muted-foreground">In plain words:</span> {trace.explanation}</p>}
              </div>
              {trace.executed_sql && (
                <div className="flex flex-wrap gap-2 text-xs">
                  {['Parsed as one SELECT', 'Read-only transaction, rolled back', 'Read-only database login'].map((s) => (
                    <span key={s} className="inline-flex items-center gap-1 rounded-full border px-2 py-0.5"><ShieldCheck className="size-3 text-primary" />{s}</span>
                  ))}
                </div>
              )}
              {turn.rows && turn.rows.length > 0 && (
                <div>
                  <Label>Result ({formatNumber(trace.sql_rows_count)} {trace.sql_rows_count === 1 ? 'row' : 'rows'})</Label>
                  <div className="overflow-auto rounded-md border">
                    <table className="w-full text-xs">
                      <thead><tr className="border-b bg-muted text-left">{turn.columns.map((c) => <th key={c} className="p-2 font-medium">{c}</th>)}</tr></thead>
                      <tbody className="font-mono">
                        {turn.rows.slice(0, 8).map((r, i) => (
                          <tr key={i} className="border-b last:border-0">{turn.columns.map((c) => <td key={c} className="p-2">{String(r[c] ?? '')}</td>)}</tr>
                        ))}
                      </tbody>
                    </table>
                  </div>
                </div>
              )}
            </StepCard>
          )}

          {/* ── Documents: search ──────────────────────────────────────────── */}
          {steps.includes('search') && (
            <StepCard id="search" number={(n += 1)} title="Documents: find the right filings, then the right passages" time={times.search} anchor={anchor}>
              {!rt ? (
                <p className="text-xs text-muted-foreground">
                  This answer carries no stage-by-stage search record. The passages it found are listed under the answer writer.
                </p>
              ) : (
                <>
                  <p className="text-xs"><span className="text-muted-foreground">Searched for:</span> {rt.search_text}</p>
                  {hybrid?.narrowed && (
                    <p className="rounded-md border border-primary/40 bg-primary/5 p-2 text-xs">
                      The database result named <span className="font-medium">{(hybrid.entities ?? []).join(', ')}</span>, so the search was limited to the {(hybrid.matched_documents ?? []).length} filings whose titles name them, and round 1 was skipped.
                    </p>
                  )}
                  <div className="grid gap-4 md:grid-cols-2">
                    <div>
                      <Label>{rt.round1.length === 0 ? 'Round 1: which filings?' : `Round 1: best ${rt.round1.length} passages across the whole library`}</Label>
                      {rt.round1.length === 0 ? (
                        <p className="text-xs text-muted-foreground">Skipped: the filings were already known.</p>
                      ) : (
                        <ol data-testid="round1" className="space-y-1 text-xs">
                          {rt.round1.map((p, i) => (
                            <li key={p.chunk_id} className="flex items-center gap-2 rounded border px-2 py-1">
                              <span className="w-4 shrink-0 text-muted-foreground">{i + 1}</span>
                              <span className="flex-1 truncate" title={passageLabel(p)}>{passageLabel(p)}</span>
                              <span className="shrink-0 font-mono text-muted-foreground">{p.score.toFixed(3)}</span>
                            </li>
                          ))}
                        </ol>
                      )}
                    </div>
                    <div>
                      <Label>Round 2: {rt.passages.length} candidate passages inside those filings</Label>
                      <ul className="space-y-1 text-xs">
                        {documentsOf(rt.passages).map((d) => (
                          <li key={d.document_id} className="flex items-center justify-between gap-2 rounded border px-2 py-1">
                            <span className="truncate" title={d.title}>{d.title}</span>
                            <span className="shrink-0 text-muted-foreground">{d.passages} {d.passages === 1 ? 'passage' : 'passages'}</span>
                          </li>
                        ))}
                      </ul>
                      <p className="mt-1.5 text-xs text-muted-foreground">
                        Round 1 picks the filings; round 2 searches again inside only those, so tables and figures that do not read like the question are still found.
                      </p>
                    </div>
                  </div>
                </>
              )}
            </StepCard>
          )}

          {/* ── Documents: re-ranking ──────────────────────────────────────── */}
          {steps.includes('rerank') && rt && (
            <StepCard id="rerank" number={(n += 1)} title="Re-ranking: which passages does the writer read?" time={times.rerank} anchor={anchor}>
              <p className="text-xs text-muted-foreground">
                Each candidate's rank at every stage. The answer writer reads the top {window}.{' '}
                {hasJevOrder && <>Jev's scoring moved <span className="font-medium text-foreground">{changes.movedIn}</span> {changes.movedIn === 1 ? 'passage' : 'passages'} into that top {window}. </>}
                Click a row to read the passage.
              </p>
              <div className="overflow-auto rounded-md border">
                <table data-testid="rank-table" className="w-full text-xs">
                  <thead>
                    <tr className="border-b bg-muted text-left">
                      <th className="w-10 p-2 font-medium">Final</th>
                      <th className="p-2 font-medium">Passage</th>
                      <th className="w-24 p-2 text-right font-medium">Dense search</th>
                      {hasJevOrder && <th className="w-24 p-2 text-right font-medium">Jev P(answer)</th>}
                      {hasJevOrder && <th className="w-16 p-2 text-right font-medium" title="Places gained or lost against the order the search returned">Moved</th>}
                    </tr>
                  </thead>
                  <tbody>
                    {rows.map((r, i) => {
                      const id = r.passage.chunk_id
                      const text = texts.get(id)
                      const isOpen = expanded === id
                      const cols = 3 + (hasJevOrder ? 2 : 0)
                      return (
                        <Fragment key={id}>
                          {i === window && (
                            <tr><td colSpan={cols} className="border-y border-dashed border-primary/60 bg-primary/5 px-2 py-1 text-[11px] text-primary">Below this line: found, but not read by the answer writer</td></tr>
                          )}
                          <tr
                            data-testid="rank-row"
                            data-read={r.read}
                            onClick={() => setExpanded(isOpen ? null : id)}
                            className={cn('cursor-pointer border-b last:border-0 hover:bg-muted/60', !r.read && 'text-muted-foreground')}
                          >
                            <td className="p-2 font-mono font-semibold">{r.final}</td>
                            <td className="p-2">
                              <span className="flex items-center gap-1">
                                {isOpen ? <ChevronDown className="size-3 shrink-0" /> : <ChevronRight className="size-3 shrink-0" />}
                                <span className="truncate" title={passageLabel(r.passage)}>{passageLabel(r.passage)}</span>
                              </span>
                            </td>
                            <td className="p-2 text-right font-mono">#{r.dense ?? '—'} <span className="text-muted-foreground">({r.passage.score.toFixed(2)})</span></td>
                            {hasJevOrder && (
                              <td className="p-2 text-right font-mono">
                                {r.jev ? <>#{r.jev} <span className="text-muted-foreground">({(r.passage.jev_score ?? 0).toFixed(2)})</span></> : 'failed'}
                              </td>
                            )}
                            {hasJevOrder && (
                              <td className="p-2 text-right font-mono">
                                {r.moved > 0 ? (
                                  <span className="inline-flex items-center gap-0.5 text-emerald-600 dark:text-emerald-400"><ArrowUp className="size-3" />{r.moved}</span>
                                ) : r.moved < 0 ? (
                                  <span className="inline-flex items-center gap-0.5 text-muted-foreground"><ArrowDown className="size-3" />{-r.moved}</span>
                                ) : '·'}
                              </td>
                            )}
                          </tr>
                          {isOpen && (
                            <tr><td colSpan={cols} className="border-b bg-muted/40 p-3">
                              <pre className="max-h-56 overflow-auto whitespace-pre-wrap font-mono text-[11px] leading-relaxed">{text ?? 'The passage text was not returned with this answer.'}</pre>
                            </td></tr>
                          )}
                        </Fragment>
                      )
                    })}
                  </tbody>
                </table>
              </div>
              <p className="text-xs text-muted-foreground">
                Dense search compares meanings as vectors.{' '}
                {hasJevOrder && `Jev answers “does this passage contain the information needed?” for each one (${rt.jev?.scored ?? 0} scored, ${rt.jev?.failed ?? 0} failed).`}
              </p>
            </StepCard>
          )}

          {/* ── Answer writer ──────────────────────────────────────────────── */}
          <StepCard id="writer" number={(n += 1)} title="Answer writer" time={times.writer} anchor={anchor}>
            {route === 'neither' ? (
              <p className="text-xs text-muted-foreground">Neither source holds this, so a fixed reply was returned with no model call.</p>
            ) : (
              <div className="grid gap-4 md:grid-cols-2">
                <div>
                  <Label>What it was given</Label>
                  <ul className="space-y-1 text-xs">
                    {usesDatabase && trace.executed_sql && <li className="rounded border px-2 py-1">The query's result: {formatNumber(trace.sql_rows_count)} {trace.sql_rows_count === 1 ? 'row' : 'rows'}</li>}
                    {readPassages.map((r) => <li key={r.passage.chunk_id} className="truncate rounded border px-2 py-1" title={passageLabel(r.passage)}>{r.final}. {passageLabel(r.passage)}</li>)}
                    {!rt && fallbackPassages.map((r, i) => {
                      const prov = ((r as { provenance?: Record<string, unknown> }).provenance ?? r) as { title?: string | null; document_id?: string; page?: number | null }
                      return <li key={i} className="truncate rounded border px-2 py-1">{i + 1}. {passageLabel({ title: prov.title ?? null, document_id: prov.document_id ?? 'document', page: prov.page ?? null })}</li>
                    })}
                  </ul>
                </div>
                <div className="space-y-3">
                  <div>
                    <Label>Sources it cited</Label>
                    {turn.citations.length > 0 ? (
                      <ul className="space-y-1 text-xs">
                        {turn.citations.map((c, i) => (
                          <li key={i} className="flex items-center gap-1.5 rounded border px-2 py-1">
                            <Check className="size-3 shrink-0 text-primary" />
                            <span className="truncate">{c.kind === 'sql' ? 'The query above' : `${c.title || c.ref}${c.page != null ? `, p. ${c.page}` : ''}`}</span>
                          </li>
                        ))}
                      </ul>
                    ) : <p className="text-xs text-muted-foreground">None.</p>}
                  </div>
                  {grounding && grounding.figures.length > 0 && (
                    <div>
                      <Label>Figures traced back to the data</Label>
                      <p className="text-xs">
                        <Badge variant={grounding.unmatched.length === 0 ? 'secondary' : 'destructive'}>{grounding.matched} of {grounding.figures.length}</Badge>{' '}
                        <span className="text-muted-foreground">{grounding.unmatched.length === 0 ? 'figures in the answer match the result or a cited passage.' : `not traced: ${grounding.unmatched.join(', ')}`}</span>
                      </p>
                    </div>
                  )}
                </div>
              </div>
            )}
          </StepCard>

          {/* ── Cost ───────────────────────────────────────────────────────── */}
          {cost.length > 0 && (
            <StepCard id="cost" number={(n += 1)} title="Every model step, in order" anchor={anchor}>
              <div className="overflow-auto rounded-md border">
                <table data-testid="cost-table" className="w-full text-xs">
                  <thead><tr className="border-b bg-muted text-left">
                    <th className="p-2 font-medium">Step</th><th className="p-2 font-medium">Model or method</th>
                    <th className="p-2 text-right font-medium">Calls</th><th className="p-2 text-right font-medium">Tokens in</th>
                    <th className="p-2 text-right font-medium">Tokens out</th><th className="p-2 text-right font-medium">Time</th>
                  </tr></thead>
                  <tbody className="font-mono">
                    {cost.map((c, i) => (
                      <tr key={i} className="border-b last:border-0">
                        <td className="p-2 font-sans">{c.step}</td><td className="p-2 break-all">{c.detail}</td>
                        <td className="p-2 text-right">{c.calls}</td>
                        <td className="p-2 text-right">{c.tokensIn ? formatNumber(c.tokensIn) : '—'}</td>
                        <td className="p-2 text-right">{c.tokensOut ? formatNumber(c.tokensOut) : '—'}</td>
                        <td className="p-2 text-right">{formatMs(c.ms)}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </StepCard>
          )}
        </div>
      </DialogContent>
    </Dialog>
  )
}
