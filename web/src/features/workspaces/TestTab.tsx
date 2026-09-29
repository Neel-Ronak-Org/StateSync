import { CheckCircle2, Play, RefreshCw, Trash2, XCircle } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import type { ExampleCheckState, ExampleInfo, SetupJob, SetupRun, SetupState } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface TestTabProps {
  setupState: SetupState
  targetSchema: string
  job: SetupJob | null
  run: SetupRun | null
  isRunning: boolean
  onStartSetup: () => Promise<unknown>
  onSetVerdict: (runId: string, position: number, verdict: 'right' | 'wrong') => Promise<unknown>
  examples: ExampleInfo[]
  isChecking: boolean
  onRecheck: () => Promise<unknown>
  onRemoveExample: (question: string) => Promise<unknown>
}

const CHECK_LABEL: Record<ExampleCheckState | 'unchecked', string> = {
  ok: 'OK',
  changed: 'Changed',
  failed: 'Failed',
  unchecked: 'Not checked yet',
}

/** The Test tab for running the 10-question acceptance test and viewing results. */
export function TestTab({
  setupState,
  targetSchema,
  job,
  run,
  isRunning,
  onStartSetup,
  onSetVerdict,
  examples,
  isChecking,
  onRecheck,
  onRemoveExample,
}: TestTabProps) {
  const canRun = setupState === 'approved' || setupState === 'tested'
  // Why the last test stopped (e.g. the model's quota ran out), when it did.
  const failure = job?.state === 'failed' ? job.error : run?.state === 'failed' ? 'The last test stopped early.' : null
  const gradedBy = run?.graded_by ?? (['olist', 'public'].includes(targetSchema) ? 'reference' : 'owner')
  const gradingText =
    gradedBy === 'reference' ? 'Graded against reference answers' : 'You grade each answer'

  const handleStartTest = async () => {
    try {
      await onStartSetup()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to start setup test.')
    }
  }

  const handleVerdict = async (position: number, verdict: 'right' | 'wrong') => {
    if (!run) return
    try {
      await onSetVerdict(run.run_id, position, verdict)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to record verdict.')
    }
  }

  const handleRecheck = async () => {
    try {
      await onRecheck()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'The re-check failed.')
    }
  }

  const handleRemove = async (question: string) => {
    try {
      await onRemoveExample(question)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to remove the approved answer.')
    }
  }

  return (
    <div className="space-y-6 py-4">
      {/* Test Controls Header */}
      <div className="flex flex-wrap items-center justify-between gap-4 rounded-xl border bg-card p-4">
        <div className="space-y-1">
          <div className="flex items-center gap-2">
            <h3 className="text-sm font-semibold text-foreground">10-Question Validation Test</h3>
            {run && run.score !== null && (
              <div className="flex items-center gap-2 ml-2">
                <span data-testid="setup-score" className="text-sm font-bold text-foreground">
                  {run.score}/{run.total}
                </span>
                <span
                  className={cn(
                    'text-xs font-medium',
                    run.score >= 8 ? 'text-emerald-500' : 'text-amber-500',
                  )}
                >
                  {run.score >= 8 ? 'Ready to use' : 'Needs work'}
                </span>
              </div>
            )}
          </div>
          <p className="text-xs text-muted-foreground">{gradingText}</p>
        </div>

        <div className="flex flex-col sm:flex-row items-start sm:items-center gap-3">
          {!canRun && (
            <span className="text-xs text-muted-foreground">Approve the knowledge first</span>
          )}

          {isRunning && (
            <span className="text-xs text-primary font-medium animate-pulse">
              Running test… {job?.done ?? 0}/{job?.total ?? 10}
            </span>
          )}

          <Button
            onClick={handleStartTest}
            disabled={!canRun || isRunning}
            className="gap-2 shrink-0"
          >
            <Play className="size-4" />
            Run the test
          </Button>
        </div>
      </div>

      {failure && (
        <p role="alert" className="rounded-lg border border-destructive/30 bg-destructive/10 px-3 py-2 text-xs text-destructive">
          {failure}
        </p>
      )}

      {/* Test Results List */}
      {run && run.results && run.results.length > 0 && (
        <div className="space-y-3">
          <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
            Test Results
          </h4>

          <div className="space-y-3">
            {run.results.map((result) => {
              // The owner grades an answer once there is one to read.
              const isOwnerGraded = gradedBy === 'owner' && result.answer_text !== null

              return (
                <div
                  key={result.position}
                  data-testid="setup-result"
                  data-verdict={result.verdict}
                  className="rounded-xl border bg-card p-4 space-y-3 transition-colors"
                >
                  <div className="flex items-start justify-between gap-3">
                    <div className="space-y-1 flex-1">
                      <div className="flex items-center gap-2">
                        <span className="font-mono text-xs text-muted-foreground">
                          Q{result.position}
                        </span>
                        <p className="text-sm font-medium text-foreground">{result.question}</p>
                      </div>

                      {result.answer_text && (
                        <p className="text-xs text-foreground/90 pl-6 leading-relaxed">
                          {result.answer_text}
                        </p>
                      )}

                      {result.detail && (
                        <p className="text-xs text-muted-foreground pl-6 font-mono">
                          {result.detail}
                        </p>
                      )}
                    </div>

                    <div className="flex items-center gap-2 shrink-0">
                      <Badge
                        variant="outline"
                        className={cn(
                          'capitalize text-xs font-normal',
                          result.verdict === 'right' &&
                            'border-emerald-500/30 bg-emerald-500/10 text-emerald-500',
                          result.verdict === 'wrong' &&
                            'border-destructive/30 bg-destructive/10 text-destructive',
                          result.verdict === 'running' &&
                            'border-primary/30 bg-primary/10 text-primary',
                          result.verdict === 'pending' && 'text-muted-foreground',
                        )}
                      >
                        {result.verdict}
                      </Badge>
                    </div>
                  </div>

                  {isOwnerGraded && (
                    <div className="flex items-center justify-end gap-2 pt-2 border-t">
                      <Button
                        type="button"
                        size="sm"
                        variant={result.verdict === 'right' ? 'default' : 'outline'}
                        aria-label={`Mark question ${result.position} right`}
                        onClick={() => handleVerdict(result.position, 'right')}
                        className="gap-1.5 h-7 text-xs"
                      >
                        <CheckCircle2 className="size-3.5" />
                        Right
                      </Button>
                      <Button
                        type="button"
                        size="sm"
                        variant={result.verdict === 'wrong' ? 'destructive' : 'outline'}
                        aria-label={`Mark question ${result.position} wrong`}
                        onClick={() => handleVerdict(result.position, 'wrong')}
                        className="gap-1.5 h-7 text-xs"
                      >
                        <XCircle className="size-3.5" />
                        Wrong
                      </Button>
                    </div>
                  )}
                </div>
              )
            })}
          </div>
        </div>
      )}

      {/* Approved answers: re-run with no model; a changed shape or an error is flagged */}
      <div className="space-y-3">
        <div className="flex flex-wrap items-center justify-between gap-3">
          <div className="space-y-1">
            <h4 className="text-xs font-semibold uppercase tracking-wider text-muted-foreground">
              Approved answers
            </h4>
            <p className="text-xs text-muted-foreground">
              Re-run without a model to catch data changes; results are compared by shape, not values.
            </p>
          </div>
          <div className="flex items-center gap-3">
            {isChecking && (
              <span role="status" className="text-xs text-primary font-medium animate-pulse">
                Checking…
              </span>
            )}
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={handleRecheck}
              disabled={isChecking || examples.length === 0}
              className="gap-1.5"
            >
              <RefreshCw className={cn('size-3.5', isChecking && 'animate-spin')} />
              Re-check
            </Button>
          </div>
        </div>

        {examples.length === 0 ? (
          <p className="rounded-xl border bg-card p-4 text-xs text-muted-foreground">
            No approved answers yet. Mark an answer correct in chat, or pass the setup test.
          </p>
        ) : (
          <div className="space-y-2">
            {examples.map((example) => {
              const state: ExampleCheckState | 'unchecked' = example.check_state ?? 'unchecked'
              return (
                <div
                  key={example.example_id}
                  data-testid="approved-answer"
                  data-state={state}
                  className="flex items-start justify-between gap-3 rounded-xl border bg-card p-3"
                >
                  <div className="min-w-0 flex-1 space-y-1">
                    <p className="text-sm font-medium text-foreground">{example.question}</p>
                    {(state === 'changed' || state === 'failed') && example.check_detail && (
                      <p className="text-xs text-muted-foreground font-mono break-words">
                        {example.check_detail}
                      </p>
                    )}
                  </div>
                  <div className="flex shrink-0 items-center gap-2">
                    <Badge
                      variant="outline"
                      className={cn(
                        'text-xs font-normal',
                        state === 'ok' && 'border-emerald-500/30 bg-emerald-500/10 text-emerald-500',
                        state === 'changed' && 'border-amber-500/30 bg-amber-500/10 text-amber-500',
                        state === 'failed' && 'border-destructive/30 bg-destructive/10 text-destructive',
                        state === 'unchecked' && 'text-muted-foreground',
                      )}
                    >
                      {CHECK_LABEL[state]}
                    </Badge>
                    <Button
                      type="button"
                      size="sm"
                      variant="ghost"
                      aria-label={`Remove approved answer ${example.question}`}
                      onClick={() => handleRemove(example.question)}
                      className="gap-1.5 h-7 text-xs"
                    >
                      <Trash2 className="size-3.5" />
                      Remove
                    </Button>
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </div>
    </div>
  )
}
