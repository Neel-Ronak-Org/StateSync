import { CheckCircle2, Circle, Loader2, MinusCircle, X } from 'lucide-react'
import { motion } from 'motion/react'

import type { BuildJob } from '@/api/types'
import { Button } from '@/components/ui/button'
import { cn } from '@/lib/utils'

interface BuildProgressProps {
  job: BuildJob
  onDismiss: () => void
}

/** Progress of an AI build (web/DESIGN.md §6): one row per planned insight. */
export function BuildProgress({ job, onDismiss }: BuildProgressProps) {
  const finished = job.state === 'done' || job.state === 'failed'
  const done = job.items.filter((i) => i.state === 'added' || i.state === 'skipped').length
  const added = job.items.filter((i) => i.state === 'added').length
  const heading =
    job.state === 'planning'
      ? 'Planning insights…'
      : job.state === 'running'
        ? `Building ${Math.min(done + 1, job.items.length)} of ${job.items.length}`
        : job.state === 'failed'
          ? 'The build failed'
          : `Added ${added} of ${job.items.length} insights`
  const progress = job.items.length ? (done / job.items.length) * 100 : 0

  return (
    <motion.section
      data-testid="build-progress"
      role="status"
      aria-live="polite"
      initial={{ opacity: 0, y: -6 }}
      animate={{ opacity: 1, y: 0 }}
      className="mx-6 mt-5 overflow-hidden rounded-xl border bg-card"
    >
      <div className="flex items-center gap-3 px-4 py-3">
        {finished ? (
          job.state === 'failed' ? <MinusCircle className="size-4 text-destructive" /> : <CheckCircle2 className="size-4 text-success" />
        ) : (
          <Loader2 className="size-4 animate-spin text-primary" />
        )}
        <div className="min-w-0 flex-1">
          <p className="text-sm font-medium">{heading}</p>
          {job.goal && <p className="truncate text-xs text-muted-foreground">Focus: {job.goal}</p>}
          {job.error && <p className="text-xs text-destructive">{job.error}</p>}
        </div>
        {finished && (
          <Button variant="ghost" size="icon-sm" aria-label="Dismiss" onClick={onDismiss}>
            <X className="size-4" />
          </Button>
        )}
      </div>
      <div className="h-0.5 bg-muted">
        <motion.div className="h-full bg-primary" animate={{ width: `${finished ? 100 : progress}%` }} transition={{ duration: 0.4 }} />
      </div>
      {job.items.length > 0 && (
        <ul className="grid gap-x-6 gap-y-1.5 px-4 py-3 text-xs sm:grid-cols-2">
          {job.items.map((item, i) => (
            <li key={i} className="flex min-w-0 items-start gap-2" data-state={item.state}>
              {item.state === 'added' && <CheckCircle2 className="mt-px size-3.5 shrink-0 text-success" />}
              {item.state === 'running' && <Loader2 className="mt-px size-3.5 shrink-0 animate-spin text-primary" />}
              {item.state === 'queued' && <Circle className="mt-px size-3.5 shrink-0 text-muted-foreground" />}
              {item.state === 'skipped' && <MinusCircle className="mt-px size-3.5 shrink-0 text-muted-foreground" />}
              <span className="min-w-0">
                <span className={cn('block truncate', item.state === 'skipped' && 'text-muted-foreground line-through')}>
                  {item.title}
                </span>
                {item.state === 'skipped' && item.reason && (
                  <span className="block truncate text-muted-foreground">{item.reason}</span>
                )}
              </span>
            </li>
          ))}
        </ul>
      )}
    </motion.section>
  )
}
