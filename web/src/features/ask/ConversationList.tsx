import { useState } from 'react'
import { Plus, Trash2 } from 'lucide-react'
import { AnimatePresence, motion } from 'motion/react'

import type { SessionSummary } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Skeleton } from '@/components/ui/skeleton'
import { formatRelativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'

interface ConversationListProps {
  sessions: SessionSummary[]
  isLoading: boolean
  activeSessionId: string
  onSelect: (sessionId: string) => void
  onNewChat: () => void
  onDelete: (sessionId: string) => Promise<void>
}

/** The Ask page's left pane (web/DESIGN.md §7): conversations, newest first, each deletable. */
export function ConversationList({ sessions, isLoading, activeSessionId, onSelect, onNewChat, onDelete }: ConversationListProps) {
  const [pending, setPending] = useState<SessionSummary | null>(null)
  const [deleting, setDeleting] = useState(false)

  const confirmDelete = async () => {
    if (!pending) return
    setDeleting(true)
    try {
      await onDelete(pending.session_id)
      setPending(null)
    } catch {
      // onDelete has said why; the dialog stays open to retry or cancel
    } finally {
      setDeleting(false)
    }
  }

  return (
    <aside className="hidden w-72 shrink-0 flex-col border-r bg-card/40 lg:flex">
      <div className="flex h-14 shrink-0 items-center border-b px-3">
        <Button variant="outline" className="h-8 w-full justify-start gap-2 text-xs font-medium" onClick={onNewChat}>
          <Plus className="size-3.5" />
          New chat
        </Button>
      </div>
      <nav aria-label="Conversations" className="flex-1 space-y-0.5 overflow-y-auto p-2">
        {isLoading && Array.from({ length: 6 }, (_, i) => <Skeleton key={i} className="h-11 rounded-lg" />)}
        {!isLoading && sessions.length === 0 && (
          <p className="px-2 py-6 text-center text-xs text-muted-foreground">No conversations yet.</p>
        )}
        <AnimatePresence initial={false}>
          {sessions.map((s) => {
            const active = s.session_id === activeSessionId
            const { relative, absolute } = formatRelativeTime(s.last_activity || s.started_at)
            return (
              <motion.div
                key={s.session_id}
                layout
                initial={{ opacity: 0 }}
                animate={{ opacity: 1 }}
                exit={{ opacity: 0, height: 0 }}
                transition={{ duration: 0.18 }}
                className={cn(
                  'group relative flex items-center rounded-lg transition-colors',
                  active ? 'bg-accent' : 'hover:bg-accent/60',
                )}
              >
                {active && <span className="absolute inset-y-2 left-0 w-0.5 rounded-full bg-primary" />}
                <button
                  type="button"
                  aria-label={s.first_query}
                  aria-current={active ? 'true' : undefined}
                  title={s.first_query}
                  onClick={() => onSelect(s.session_id)}
                  className="min-w-0 flex-1 rounded-lg px-3 py-2 text-left outline-none focus-visible:ring-2 focus-visible:ring-ring"
                >
                  <span className="block truncate text-xs font-medium text-foreground">{s.first_query}</span>
                  <span className="mt-0.5 flex items-center gap-1.5 text-[11px] text-muted-foreground">
                    <span title={absolute}>{relative}</span>
                    {s.target_schema && <span>· {s.target_schema}</span>}
                  </span>
                </button>
                <Button
                  variant="ghost"
                  size="icon-sm"
                  aria-label="Delete conversation"
                  onClick={() => setPending(s)}
                  className="mr-1 shrink-0 text-muted-foreground opacity-0 transition-opacity group-hover:opacity-100 focus-visible:opacity-100 hover:text-destructive"
                >
                  <Trash2 className="size-3.5" />
                </Button>
              </motion.div>
            )
          })}
        </AnimatePresence>
      </nav>

      <Dialog open={pending !== null} onOpenChange={(open) => !open && setPending(null)}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete this conversation?</DialogTitle>
            <DialogDescription>
              Its questions and answers are removed. Tiles pinned from it keep working.
            </DialogDescription>
          </DialogHeader>
          {pending && <p className="truncate rounded-md bg-muted px-3 py-2 text-xs">{pending.first_query}</p>}
          <DialogFooter>
            <Button variant="outline" onClick={() => setPending(null)}>Cancel</Button>
            <Button variant="destructive" onClick={() => void confirmDelete()} disabled={deleting}>Delete</Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </aside>
  )
}
