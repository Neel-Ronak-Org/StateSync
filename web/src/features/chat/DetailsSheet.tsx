import { useState } from 'react'
import { Check, Copy } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import {
  Sheet,
  SheetContent,
  SheetDescription,
  SheetHeader,
  SheetTitle,
} from '@/components/ui/sheet'
import { useDatasetLabel } from '@/features/library/useConnections'
import { copyToClipboard } from '@/lib/clipboard'
import { formatNumber } from '@/lib/format'
import { sourceLabel } from '@/lib/sources'
import type { ModelCall } from '@/api/types'
import type { ChatTurn } from '@/types/chat'

interface DetailsSheetProps {
  turn: ChatTurn | null
  open: boolean
  onOpenChange: (open: boolean) => void
  devMode: boolean
}

function formatRoute(route: string | null | undefined): string {
  if (route === 'sql') return 'Database'
  if (route === 'docs') return 'Documents'
  if (route === 'hybrid') return 'Database + documents'
  return '—'
}

export function DetailsSheet({ turn, open, onOpenChange, devMode }: DetailsSheetProps) {
  const [copiedSql, setCopiedSql] = useState(false)
  const datasetLabel = useDatasetLabel([turn?.dataset])

  if (!turn) return null

  const handleCopySql = async () => {
    if (!turn.sql) return
    const ok = await copyToClipboard(turn.sql)
    if (ok) {
      setCopiedSql(true)
      toast.success('SQL copied to clipboard')
      setTimeout(() => setCopiedSql(false), 2000)
    } else {
      toast.error('Copy failed. Select the text and copy it manually.')
    }
  }

  const metrics = turn.stateTrace?.metrics
  const calls = turn.stateTrace?.calls ?? []
  const anyWaited = calls.some((c) => (c.waited_ms ?? 0) > 0)
  const sum = (pick: (c: ModelCall) => number | undefined) =>
    calls.reduce((total, c) => total + (pick(c) ?? 0), 0)

  return (
    <Sheet open={open} onOpenChange={onOpenChange}>
      <SheetContent side="right" className="sm:max-w-lg overflow-y-auto">
        <SheetHeader className="pb-2">
          <SheetTitle>Answer details</SheetTitle>
          <SheetDescription className="text-xs">
            Provenance, metrics, and trace for turn #{turn.turnIndex}.
          </SheetDescription>
        </SheetHeader>

        <div className="space-y-6 px-4 pb-6">
          {/* Section 1: Summary */}
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-foreground">Summary</h3>
            <div className="grid grid-cols-2 gap-2 text-xs">
              <div className="rounded-md border p-2">
                <span className="text-muted-foreground block">Dataset</span>
                <span className="font-medium text-foreground">{datasetLabel(turn.dataset)}</span>
              </div>
              <div className="rounded-md border p-2">
                <span className="text-muted-foreground block">Route</span>
                <span className="font-medium text-foreground">{formatRoute(turn.route)}</span>
              </div>
              <div className="rounded-md border p-2">
                <span className="text-muted-foreground block">Turn number</span>
                <span className="font-medium text-foreground font-mono">{turn.turnIndex}</span>
              </div>
              <div className="rounded-md border p-2">
                <span className="text-muted-foreground block">Latency</span>
                <span className="font-medium text-foreground font-mono">
                  {(turn.latencyMs / 1000).toFixed(1)} s
                </span>
              </div>
              <div className="rounded-md border p-2">
                <span className="text-muted-foreground block">LLM calls</span>
                <span className="font-medium text-foreground font-mono">
                  {metrics?.llm_calls != null ? formatNumber(metrics.llm_calls) : '—'}
                </span>
              </div>
              <div className="rounded-md border p-2">
                <span className="text-muted-foreground block">Tokens in / out</span>
                <span className="font-medium text-foreground font-mono">
                  {metrics?.tokens_in != null && metrics?.tokens_out != null
                    ? `${formatNumber(metrics.tokens_in)} / ${formatNumber(metrics.tokens_out)}`
                    : '—'}
                </span>
              </div>
            </div>
          </div>

          {/* Section 2: SQL */}
          <div className="space-y-2">
            <div className="flex items-center justify-between">
              <h3 className="text-sm font-medium text-foreground">SQL</h3>
              {turn.sql && (
                <Button
                  variant="ghost"
                  size="xs"
                  onClick={handleCopySql}
                  className="h-6 gap-1 text-xs"
                >
                  {copiedSql ? <Check className="size-3" /> : <Copy className="size-3" />}
                  <span>{copiedSql ? 'Copied' : 'Copy'}</span>
                </Button>
              )}
            </div>
            {turn.sql ? (
              <pre className="font-mono text-xs bg-muted rounded-md p-3 overflow-auto max-h-60 whitespace-pre-wrap leading-relaxed">
                {turn.sql}
              </pre>
            ) : (
              <p className="text-xs text-muted-foreground">No SQL was run for this answer.</p>
            )}
          </div>

          {/* Section: Chart spec (Developer Mode) */}
          {devMode && turn.chart && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-foreground">Chart spec</h3>
              <pre className="max-h-72 overflow-auto rounded-md bg-muted p-3 font-mono text-xs">
                {JSON.stringify(turn.chart, null, 2).slice(0, 4000)}
              </pre>
            </div>
          )}

          {/* Section 3: Sources */}
          <div className="space-y-2">
            <h3 className="text-sm font-medium text-foreground">Sources</h3>
            {turn.citations && turn.citations.length > 0 ? (
              <div className="space-y-1.5">
                {turn.citations.map((c, idx) => (
                  <div key={idx} className="rounded-md border p-2 text-xs font-mono">
                    <span className="text-muted-foreground block text-[10px] uppercase">
                      {c.kind}
                    </span>
                    {c.kind === 'sql' ? (
                      <span className="text-foreground break-words">SQL query — shown above</span>
                    ) : (
                      <>
                        <span className="text-foreground break-words" title={c.ref}>
                          {c.title || sourceLabel(c.ref)}
                        </span>
                        {c.page != null && (
                          <span className="text-muted-foreground ml-1">p. {c.page}</span>
                        )}
                      </>
                    )}
                  </div>
                ))}
              </div>
            ) : (
              <p className="text-xs text-muted-foreground">No citations for this answer.</p>
            )}
          </div>

          {/* Section: Model calls (Developer Mode) */}
          {devMode && calls.length > 0 && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-foreground">Model calls</h3>
              <div className="overflow-x-auto rounded-md border">
                <table data-testid="model-calls" className="w-full text-xs">
                  <thead className="text-muted-foreground">
                    <tr className="border-b text-left">
                      <th className="p-2 font-medium">Step</th>
                      <th className="p-2 font-medium">Model</th>
                      <th className="p-2 text-right font-medium">In</th>
                      <th className="p-2 text-right font-medium">Out</th>
                      <th className="p-2 text-right font-medium">Cached</th>
                      <th className="p-2 text-right font-medium">Time</th>
                      {anyWaited && <th className="p-2 text-right font-medium">Waited</th>}
                    </tr>
                  </thead>
                  <tbody className="font-mono">
                    {calls.map((c, idx) => (
                      <tr key={idx} data-testid="model-call" className="border-b">
                        <td className="p-2">{c.role}</td>
                        <td className="p-2 break-all">{c.model}</td>
                        {c.error ? (
                          <td colSpan={3} className="p-2 text-right text-destructive">
                            failed: {c.error}
                          </td>
                        ) : (
                          <>
                            <td className="p-2 text-right">{formatNumber(c.tokens_in ?? 0)}</td>
                            <td className="p-2 text-right">{formatNumber(c.tokens_out ?? 0)}</td>
                            <td className="p-2 text-right">{formatNumber(c.cached_tokens ?? 0)}</td>
                          </>
                        )}
                        <td className="p-2 text-right">{(c.ms / 1000).toFixed(2)} s</td>
                        {anyWaited && (
                          <td className="p-2 text-right">
                            {c.waited_ms ? `${(c.waited_ms / 1000).toFixed(2)} s` : '—'}
                          </td>
                        )}
                      </tr>
                    ))}
                  </tbody>
                  <tfoot className="font-mono font-medium">
                    <tr data-testid="model-calls-total">
                      <td className="p-2" colSpan={2}>Total</td>
                      <td className="p-2 text-right">{formatNumber(sum((c) => c.tokens_in))}</td>
                      <td className="p-2 text-right">{formatNumber(sum((c) => c.tokens_out))}</td>
                      <td className="p-2 text-right">{formatNumber(sum((c) => c.cached_tokens))}</td>
                      <td className="p-2 text-right">{(sum((c) => c.ms) / 1000).toFixed(2)} s</td>
                      {anyWaited && (
                        <td className="p-2 text-right">
                          {(sum((c) => c.waited_ms) / 1000).toFixed(2)} s
                        </td>
                      )}
                    </tr>
                  </tfoot>
                </table>
              </div>
            </div>
          )}

          {/* Section 4: Trace (Developer Mode) */}
          {devMode && turn.stateTrace && (
            <div className="space-y-2">
              <h3 className="text-sm font-medium text-foreground">Trace</h3>
              <div className="space-y-2 font-mono text-xs">
                {Object.entries(turn.stateTrace).map(([key, value]) => {
                  const valStr = typeof value === 'string' ? value : JSON.stringify(value)
                  const truncated = valStr.length > 300 ? `${valStr.slice(0, 300)}…` : valStr
                  return (
                    <div key={key} className="rounded-md border bg-muted/40 p-2">
                      <div className="font-semibold text-foreground">{key}</div>
                      <div className="text-muted-foreground break-words whitespace-pre-wrap mt-0.5">
                        {truncated}
                      </div>
                    </div>
                  )
                })}
              </div>
            </div>
          )}
        </div>
      </SheetContent>
    </Sheet>
  )
}
