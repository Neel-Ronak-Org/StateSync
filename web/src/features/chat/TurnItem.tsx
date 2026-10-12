import { useId, useMemo, useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { AlertTriangle, Download, MoreHorizontal, PanelRight, Pin, ThumbsDown, ThumbsUp, Workflow } from 'lucide-react'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { StateTrace, Tile } from '@/api/types'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { VegaChart, type ChartView } from '@/charts/VegaChart'
import { answerBlocks } from '@/features/chat/answerBlocks'
import { figureCursor, figureSource, type Segment } from '@/features/chat/figures'
import { ResultTable } from '@/features/chat/ResultTable'
import { useAllTiles } from '@/features/dashboard/useTiles'
import { useDatasetLabel } from '@/features/library/useConnections'
import { copyToClipboard } from '@/lib/clipboard'
import { downloadCsv } from '@/lib/csv'
import { sourceLabel } from '@/lib/sources'
import type { ChatTurn } from '@/types/chat'
import { resolveKind, shapeOf, VEGA_KINDS } from '@/viz/infer'
import { buildSpec } from '@/viz/spec'

interface TurnItemProps {
  turn: ChatTurn
  devMode: boolean
  onOpenDetails: (turn: ChatTurn) => void
  onOpenInspector: (turn: ChatTurn) => void
  onRetry: (turn: ChatTurn) => void
  onUpdateTurn: (turnId: string, updates: Partial<ChatTurn>) => void
  sessionId: string
}

function formatRoute(route: string | null | undefined): string {
  if (route === 'sql') return 'Database'
  if (route === 'docs') return 'Documents'
  if (route === 'hybrid') return 'Database + documents'
  return ''
}

type Evidence = NonNullable<StateTrace['evidence']>

const GREEN_CHIP = 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500 font-normal text-xs'

/** The chip for each evidence tier: what to show and the one-line reason (orchestrator/evidence.py). */
const EVIDENCE_CHIPS: Record<Evidence, { label: string; className: string; hint: string }> = {
  verified: {
    label: 'Verified',
    className: GREEN_CHIP,
    hint: 'Answered with a query approved for this workspace.',
  },
  compiled: {
    label: 'Compiled',
    className: 'border-sky-500/30 bg-sky-500/10 text-sky-500 font-normal text-xs',
    hint: "Built from an approved query pattern, with this question's values.",
  },
  checked: {
    label: 'Checked',
    className: 'border-sky-500/30 bg-sky-500/10 text-sky-500 font-normal text-xs',
    hint: 'New query; it passed the automatic checks.',
  },
  unchecked: {
    label: 'Unchecked',
    className: 'font-normal text-xs text-muted-foreground',
    hint: 'New query; not independently checked.',
  },
}

/** A chunk of answer text with each figure marked: dotted when it matches its source, amber when it does not. */
function renderFigures(segments: (chunk: string) => Segment[], chunk: string) {
  return segments(chunk).map((seg, i) =>
    seg.figure ? (
      <span
        key={i}
        data-testid="figure"
        data-matched={seg.figure.matched ? 'true' : 'false'}
        title={figureSource(seg.figure)}
        className={
          seg.figure.matched
            ? 'underline decoration-dotted decoration-muted-foreground/60 underline-offset-2'
            : 'rounded-sm bg-destructive/10 px-0.5 text-destructive underline decoration-dotted underline-offset-2'
        }
      >
        {seg.text}
      </span>
    ) : (
      <span key={i}>{seg.text}</span>
    ),
  )
}

export function TurnItem({
  turn,
  devMode,
  onOpenDetails,
  onOpenInspector,
  onRetry,
  onUpdateTurn,
  sessionId,
}: TurnItemProps) {
  const chartViewRef = useRef<ChartView | null>(null)
  const [isLoadingFull, setIsLoadingFull] = useState(false)
  const [isPinning, setIsPinning] = useState(false)
  const [showHow, setShowHow] = useState(false)
  const [rated, setRated] = useState(false)
  const workspaceId = turn.stateTrace?.workspace_id ?? null
  const howId = useId()
  const queryClient = useQueryClient()

  const { data: tilesData } = useAllTiles()
  const datasetLabel = useDatasetLabel([turn.dataset])

  const isPinned = Boolean(
    tilesData?.tiles?.some(
      (t) => t.source?.session_id === sessionId && t.source?.turn_index === turn.turnIndex
    )
  )

  const blocks = turn.answer ? answerBlocks(turn.answer) : []
  const grounding = turn.stateTrace?.number_grounding ?? null

  // The short answer writer sends no chart: draw one here from the rows with the
  // same presentation code the dashboard tiles use. VegaChart still runs the A7
  // check on it. One row is an answer, not a chart.
  const clientChart = useMemo(() => {
    if (turn.chart || turn.stateTrace?.synth_style !== 'short') return null
    if (!turn.rows || turn.rows.length < 2) return null
    const shape = shapeOf(turn.rows, turn.columns.length > 0 ? turn.columns : null)
    const kind = resolveKind(null, shape)
    return VEGA_KINDS.has(kind) ? buildSpec(kind, shape) : null
  }, [turn.chart, turn.stateTrace?.synth_style, turn.rows, turn.columns])
  const chart = turn.chart ?? clientChart

  const handlePin = async () => {
    setIsPinning(true)
    try {
      const res = await api.pinTile({
        session_id: sessionId,
        turn_index: turn.turnIndex,
        workspace_id: workspaceId ?? undefined,
      })
      if (res.example?.saved) setRated(true)
      toast.success(res.example?.saved ? 'Pinned to dashboard and saved as an approved answer' : 'Pinned to dashboard', {
        action: {
          label: 'View',
          onClick: () => {
            window.location.hash = '#/dashboards'
          },
        },
      })
      queryClient.setQueryData<{ tiles: Tile[] }>(['tiles', 'all'], (old) =>
        old ? { tiles: [...old.tiles.filter((t) => t.tile_id !== res.tile.tile_id), res.tile] } : { tiles: [res.tile] })
      void queryClient.invalidateQueries({ queryKey: ['tiles', res.tile.dashboard_id] })
      // The pin may have created the dataset's first dashboard, and it changes a tile count.
      void queryClient.invalidateQueries({ queryKey: ['dashboards'] })
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to pin to dashboard.')
    } finally {
      setIsPinning(false)
    }
  }

  const evidence: Evidence | null =
    turn.stateTrace?.evidence ?? (turn.verifiedExample ? 'verified' : null)
  const chip = evidence ? EVIDENCE_CHIPS[evidence] : null
  // Thumbs only where an approved answer can be made or withdrawn: a workspace answer that ran SQL
  // and nothing else (a hybrid answer's SQL covers only part of its question).
  const executedSql = turn.stateTrace?.executed_sql ?? null
  const canRate = Boolean(workspaceId && executedSql && turn.stateTrace?.route === 'sql')

  const handleRate = async (correct: boolean) => {
    if (!workspaceId || !executedSql) return
    // The standalone form of the question is what the fast path matches next time.
    const question = turn.stateTrace?.normalized_query || turn.question
    try {
      if (correct) {
        await api.addExample(workspaceId, { question, sql: executedSql })
        toast.success('Saved as an approved answer for this workspace')
      } else if (evidence === 'verified') {
        await api.removeExample(workspaceId, question)
        toast.success('Removed the approved answer; the next time it will be worked out again')
      } else {
        toast.success('Thanks — noted')
      }
      setRated(true)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not save your rating.')
    }
  }

  const handleDownloadCsv = () => {
    if (!turn.rows || !turn.rows.length) return
    const cols = turn.columns.length > 0 ? turn.columns : Object.keys(turn.rows[0])
    const filename = `statesync-${turn.id}`
    downloadCsv(cols, turn.rows, filename)
  }

  const handleDownloadJson = () => {
    const data = JSON.stringify(turn, null, 2)
    const blob = new Blob([data], { type: 'application/json' })
    const url = URL.createObjectURL(blob)
    const a = document.createElement('a')
    a.href = url
    a.download = `statesync-${turn.id}.json`
    document.body.appendChild(a)
    a.click()
    document.body.removeChild(a)
    URL.revokeObjectURL(url)
  }

  const handleDownloadPng = async () => {
    if (!chartViewRef.current) return
    try {
      const url = await chartViewRef.current.toImageURL('png')
      const a = document.createElement('a')
      a.href = url
      a.download = `statesync-${turn.id}.png`
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
    } catch (err) {
      console.error('PNG export failed', err)
      toast.error('Failed to export chart as PNG.')
    }
  }

  const handleCopySql = async () => {
    if (!turn.sql) return
    const ok = await copyToClipboard(turn.sql)
    if (ok) {
      toast.success('SQL copied to clipboard')
    } else {
      toast.error('Copy failed. Select the text and copy it manually.')
    }
  }

  const handleCopyAnswer = async () => {
    if (!turn.answer) return
    const ok = await copyToClipboard(turn.answer)
    if (ok) {
      toast.success('Answer copied to clipboard')
    } else {
      toast.error('Copy failed. Select the text and copy it manually.')
    }
  }

  const handleLoadFullResult = async () => {
    setIsLoadingFull(true)
    try {
      const res = await api.turnRows(sessionId, turn.turnIndex)
      if (res.available) {
        // Keep sqlRowsCount: it is how many rows the query returned. If the
        // history store kept fewer (its row/byte caps), the table must say so
        // rather than present the stored rows as the whole result.
        onUpdateTurn(turn.id, { rows: res.rows, fullResultLoaded: true })
      } else {
        toast.info('The full result is no longer stored; showing the preview.')
      }
    } catch (err) {
      console.error('Failed to load full result', err)
      toast.error('Failed to load full result.')
    } finally {
      setIsLoadingFull(false)
    }
  }

  const hasArtifacts = Boolean(chart || (turn.rows && turn.rows.length > 0))
  const routeLabel = formatRoute(turn.route)
  const latencyLabel = turn.latencyMs ? `${(turn.latencyMs / 1000).toFixed(1)} s` : null

  return (
    <div data-testid="turn" className="flex flex-col gap-3 py-2">
      {/* Question */}
      <div className="flex justify-end">
        <div className="max-w-[85%] rounded-lg bg-muted px-3 py-2 text-sm text-foreground leading-relaxed">
          {turn.question}
        </div>
      </div>

      {/* Pending status */}
      {turn.status === 'pending' && (
        <div role="status" className="flex flex-col gap-2 py-2">
          <span className="text-sm text-muted-foreground">Thinking…</span>
          <Skeleton className="h-4 w-3/4" />
          <Skeleton className="h-4 w-1/2" />
        </div>
      )}

      {/* Error status */}
      {turn.status === 'error' && (
        <div className="space-y-2">
          <Alert variant="destructive">
            <AlertDescription className="text-xs">
              {turn.error || 'Something went wrong.'}
            </AlertDescription>
          </Alert>
          <Button variant="outline" size="sm" onClick={() => onRetry(turn)} className="text-xs">
            Retry
          </Button>
        </div>
      )}

      {/* Success answer content */}
      {turn.status === 'done' && (
        <div className="space-y-3">
          {/* Answer text */}
          <div data-testid="answer-text" className="text-sm leading-6 space-y-3 text-foreground">
            {(() => {
              const segments = figureCursor(grounding?.figures)
              const draw = (chunk: string) => renderFigures(segments, chunk)
              return blocks.map((b, idx) => {
                if (b.kind === 'p') {
                  return <p key={idx}>{draw(b.text)}</p>
                }
                if (b.kind === 'ul') {
                  return (
                    <ul key={idx} className="list-disc space-y-1 pl-5">
                      {b.items.map((item, iIdx) => (
                        <li key={iIdx}>{draw(item)}</li>
                      ))}
                    </ul>
                  )
                }
                return (
                  <ol key={idx} className="list-decimal space-y-1 pl-5">
                    {b.items.map((item, iIdx) => (
                      <li key={iIdx}>{draw(item)}</li>
                    ))}
                  </ol>
                )
              })
            })()}
          </div>

          {/* Every figure checked against the result (number_grounding.py); unmatched ones also arrive as a caveat. */}
          {grounding && grounding.figures.length > 0 && grounding.unmatched.length === 0 && (
            <p data-testid="figures-check" className="text-xs text-muted-foreground">
              {grounding.figures.length === 1
                ? 'The figure in this answer matches the result.'
                : `All ${grounding.figures.length} figures in this answer match the result.`}
            </p>
          )}

          {/* How I got this: deterministic plain-English reading of the SQL, collapsed by default */}
          {turn.stateTrace?.explanation && (
            <div className="text-xs text-muted-foreground">
              <button
                type="button"
                aria-expanded={showHow}
                aria-controls={howId}
                onClick={() => setShowHow((v) => !v)}
                className="font-medium underline-offset-2 hover:underline"
              >
                How I got this
              </button>
              {showHow && (
                <p id={howId} data-testid="how-i-got-this" className="mt-1 leading-relaxed">
                  {turn.stateTrace.explanation}
                </p>
              )}
            </div>
          )}

          {/* Caveats: the result ran, but may mislead (join fan-out, truncation, all-NULL) */}
          {turn.stateTrace?.caveats?.map((caveat, idx) => (
            <p
              key={idx}
              data-testid="answer-caveat"
              className="flex items-start gap-1.5 text-xs text-muted-foreground"
            >
              <AlertTriangle className="mt-0.5 size-3.5 shrink-0" aria-hidden="true" />
              <span>{caveat}</span>
            </p>
          ))}

          {/* Artifact card */}
          {hasArtifacts && (
            <div className="rounded-lg border bg-card p-4 space-y-4">
              {chart && (
                <VegaChart
                  spec={chart}
                  height={260}
                  onView={(view) => {
                    chartViewRef.current = view
                  }}
                  showViolations={devMode}
                />
              )}
              {turn.rows && turn.rows.length > 0 && (
                <ResultTable
                  rows={turn.rows}
                  columns={turn.columns}
                  sqlRowsCount={turn.sqlRowsCount}
                  fullResultLoaded={Boolean(turn.fullResultLoaded)}
                  onLoadFullResult={handleLoadFullResult}
                  isLoadingFull={isLoadingFull}
                />
              )}
            </div>
          )}

          {/* Sources / Citations */}
          {turn.citations && turn.citations.length > 0 && (
            <div data-testid="citations" className="flex flex-wrap items-center gap-1.5 text-xs">
              <span className="font-medium text-muted-foreground">Sources:</span>
              {turn.citations.map((c, idx) => {
                if (c.kind === 'sql') {
                  return (
                    <button
                      key={idx}
                      type="button"
                      onClick={() => onOpenDetails(turn)}
                      className="inline-flex items-center rounded-md border border-border px-2 py-0.5 text-xs font-medium hover:bg-muted transition-colors text-foreground"
                    >
                      SQL query
                    </button>
                  )
                }
                return (
                  <Badge
                    key={idx}
                    variant="outline"
                    title={c.ref}
                    className="text-xs font-normal"
                  >
                    {c.title || sourceLabel(c.ref)}
                    {c.page != null ? ` p. ${c.page}` : ''}
                  </Badge>
                )
              })}
            </div>
          )}

          {/* Meta + Actions row */}
          <div className="flex items-center justify-between text-xs text-muted-foreground pt-1">
            <div className="flex items-center gap-2">
              <Badge variant="outline" data-testid="turn-dataset" className="font-normal text-xs">
                {datasetLabel(turn.dataset)}
              </Badge>
              {chip && evidence && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Badge
                      data-testid="evidence-chip"
                      data-evidence={evidence}
                      variant="outline"
                      className={chip.className}
                    >
                      {/* One element carries one test id: the older Verified tests look for this inner label. */}
                      {evidence === 'verified' ? <span data-testid="verified-badge">{chip.label}</span> : chip.label}
                    </Badge>
                  </TooltipTrigger>
                  <TooltipContent>{chip.hint}</TooltipContent>
                </Tooltip>
              )}
              {routeLabel && <span>{routeLabel}</span>}
              {latencyLabel && <span>{latencyLabel}</span>}
            </div>

            <div className="flex items-center gap-1">
              {canRate && (
                <>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Mark answer correct"
                        disabled={rated}
                        onClick={() => void handleRate(true)}
                      >
                        <ThumbsUp className="size-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Correct: approve this answer for the workspace</TooltipContent>
                  </Tooltip>
                  <Tooltip>
                    <TooltipTrigger asChild>
                      <Button
                        variant="ghost"
                        size="icon-sm"
                        aria-label="Mark answer wrong"
                        disabled={rated}
                        onClick={() => void handleRate(false)}
                      >
                        <ThumbsDown className="size-4" />
                      </Button>
                    </TooltipTrigger>
                    <TooltipContent>Wrong</TooltipContent>
                  </Tooltip>
                </>
              )}
              {turn.sql && !isPinned && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Pin to dashboard"
                      disabled={isPinning}
                      onClick={handlePin}
                    >
                      <Pin className="size-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Pin to dashboard</TooltipContent>
                </Tooltip>
              )}

              <Tooltip>
                <TooltipTrigger asChild>
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    aria-label="Details"
                    onClick={() => onOpenDetails(turn)}
                  >
                    <PanelRight className="size-4" />
                  </Button>
                </TooltipTrigger>
                <TooltipContent>Details</TooltipContent>
              </Tooltip>

              {turn.stateTrace && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Inspect pipeline"
                      onClick={() => onOpenInspector(turn)}
                    >
                      <Workflow className="size-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Inspect: how this answer was produced</TooltipContent>
                </Tooltip>
              )}

              {turn.rows && turn.rows.length > 0 && (
                <Tooltip>
                  <TooltipTrigger asChild>
                    <Button
                      variant="ghost"
                      size="icon-sm"
                      aria-label="Download CSV"
                      onClick={handleDownloadCsv}
                    >
                      <Download className="size-4" />
                    </Button>
                  </TooltipTrigger>
                  <TooltipContent>Download CSV</TooltipContent>
                </Tooltip>
              )}

              <DropdownMenu>
                <Tooltip>
                  <TooltipTrigger asChild>
                    <DropdownMenuTrigger asChild>
                      <Button variant="ghost" size="icon-sm" aria-label="More actions">
                        <MoreHorizontal className="size-4" />
                      </Button>
                    </DropdownMenuTrigger>
                  </TooltipTrigger>
                  <TooltipContent>More actions</TooltipContent>
                </Tooltip>
                <DropdownMenuContent align="end">
                  <DropdownMenuItem onClick={handleDownloadJson}>Download JSON</DropdownMenuItem>
                  {chart && (
                    <DropdownMenuItem onClick={handleDownloadPng}>Download PNG</DropdownMenuItem>
                  )}
                  {turn.sql && <DropdownMenuItem onClick={handleCopySql}>Copy SQL</DropdownMenuItem>}
                  <DropdownMenuItem onClick={handleCopyAnswer}>Copy answer</DropdownMenuItem>
                </DropdownMenuContent>
              </DropdownMenu>
            </div>
          </div>
        </div>
      )}
    </div>
  )
}
