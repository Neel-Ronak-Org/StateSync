import { useRef, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { Download, MoreHorizontal, PanelRight, Pin } from 'lucide-react'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { Tile } from '@/api/types'
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
import { ResultTable } from '@/features/chat/ResultTable'
import { useAllTiles } from '@/features/dashboard/useTiles'
import { copyToClipboard } from '@/lib/clipboard'
import { downloadCsv } from '@/lib/csv'
import type { ChatTurn } from '@/types/chat'

interface TurnItemProps {
  turn: ChatTurn
  devMode: boolean
  onOpenDetails: (turn: ChatTurn) => void
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

export function TurnItem({
  turn,
  devMode,
  onOpenDetails,
  onRetry,
  onUpdateTurn,
  sessionId,
}: TurnItemProps) {
  const chartViewRef = useRef<ChartView | null>(null)
  const [isLoadingFull, setIsLoadingFull] = useState(false)
  const [isPinning, setIsPinning] = useState(false)
  const queryClient = useQueryClient()

  const { data: tilesData } = useAllTiles()

  const isPinned = Boolean(
    tilesData?.tiles?.some(
      (t) => t.source?.session_id === sessionId && t.source?.turn_index === turn.turnIndex
    )
  )

  const paragraphs = turn.answer ? turn.answer.split(/\n\n+/) : []

  const handlePin = async () => {
    setIsPinning(true)
    try {
      const res = await api.pinTile({
        session_id: sessionId,
        turn_index: turn.turnIndex,
      })
      toast.success('Pinned to dashboard', {
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

  const hasArtifacts = Boolean(turn.chart || (turn.rows && turn.rows.length > 0))
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
            {paragraphs.map((p, idx) => (
              <p key={idx}>{p}</p>
            ))}
          </div>

          {/* Artifact card */}
          {hasArtifacts && (
            <div className="rounded-lg border bg-card p-4 space-y-4">
              {turn.chart && (
                <VegaChart
                  spec={turn.chart}
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
                  <Badge key={idx} variant="outline" className="font-mono text-xs font-normal">
                    {c.ref}
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
                {turn.dataset}
              </Badge>
              {routeLabel && <span>{routeLabel}</span>}
              {latencyLabel && <span>{latencyLabel}</span>}
            </div>

            <div className="flex items-center gap-1">
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
                  {turn.chart && (
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
