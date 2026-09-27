import { ArrowDownRight, ArrowUpRight, Sparkles } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'

import { cn } from '@/lib/utils'
import type { Insight } from '@/viz/insights'

/** One-line insights from the tiles' own rows (web/DESIGN.md §6). Hidden when there are none. */
export function InsightsStrip({ insights }: { insights: Insight[] }) {
  if (insights.length === 0) return null
  return (
    <section aria-label="Insights" className="grid gap-3 px-6 pt-5 sm:grid-cols-2 xl:grid-cols-4">
      <AnimatePresence initial={false}>
        {insights.map((insight, i) => {
          const Icon = insight.tone === 'up' ? ArrowUpRight : insight.tone === 'down' ? ArrowDownRight : Sparkles
          return (
            <motion.div
              key={`${insight.tileId}-${insight.text}`}
              data-testid="insight"
              layout
              initial={{ opacity: 0, y: 6 }}
              animate={{ opacity: 1, y: 0 }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.25, delay: i * 0.04 }}
              className="flex min-w-0 items-start gap-2.5 rounded-xl border bg-card px-3.5 py-3"
            >
              <span
                className={cn(
                  'mt-px grid size-6 shrink-0 place-items-center rounded-full',
                  insight.tone === 'up' && 'bg-success/12 text-success',
                  insight.tone === 'down' && 'bg-destructive/12 text-destructive',
                  insight.tone === 'neutral' && 'bg-primary/12 text-primary',
                )}
              >
                <Icon className="size-3.5" aria-hidden />
              </span>
              <p className="min-w-0 text-xs leading-5 text-foreground/90">{insight.text}</p>
            </motion.div>
          )
        })}
      </AnimatePresence>
    </section>
  )
}
