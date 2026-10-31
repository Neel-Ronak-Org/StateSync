import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Copy } from 'lucide-react'
import { toast } from 'sonner'

import { Button } from '@/components/ui/button'
import { Composer } from '@/features/chat/Composer'
import { DetailsSheet } from '@/features/chat/DetailsSheet'
import { InspectorDialog } from '@/features/inspect/InspectorDialog'
import { EmptyState } from '@/features/chat/EmptyState'
import { TurnItem } from '@/features/chat/TurnItem'
import { copyToClipboard } from '@/lib/clipboard'
import type { ChatTurn } from '@/types/chat'

interface ChatViewProps {
  turns: ChatTurn[]
  onSend: (question: string) => void
  onRetry: (turn: ChatTurn) => void
  onUpdateTurn: (turnId: string, updates: Partial<ChatTurn>) => void
  isPending: boolean
  dataset: string
  devMode: boolean
  sessionId: string
  /** Rendered in the header before "Copy conversation" (the dataset picker). */
  headerExtra?: ReactNode
}

export function ChatView({
  turns,
  onSend,
  onRetry,
  onUpdateTurn,
  isPending,
  dataset,
  devMode,
  sessionId,
  headerExtra,
}: ChatViewProps) {
  const [activeDetailsTurn, setActiveDetailsTurn] = useState<ChatTurn | null>(null)
  const [detailsOpen, setDetailsOpen] = useState(false)
  const [inspectTurn, setInspectTurn] = useState<ChatTurn | null>(null)
  const [inspectOpen, setInspectOpen] = useState(false)
  const scrollRef = useRef<HTMLDivElement>(null)

  // Scroll to bottom when new turns arrive
  useEffect(() => {
    if (scrollRef.current) {
      scrollRef.current.scrollTop = scrollRef.current.scrollHeight
    }
  }, [turns.length, isPending])

  const handleOpenDetails = (turn: ChatTurn) => {
    setActiveDetailsTurn(turn)
    setDetailsOpen(true)
  }

  const handleCopyConversation = async () => {
    if (turns.length === 0) {
      toast.info('No conversation to copy.')
      return
    }
    const blocks: string[] = []
    for (const turn of turns) {
      if (turn.status === 'error') continue
      let block = `## ${turn.question}\n\n${turn.answer}`
      if (turn.sql) {
        block += `\n\n\`\`\`sql\n${turn.sql}\n\`\`\``
      }
      blocks.push(block)
    }
    const ok = await copyToClipboard(blocks.join('\n\n'))
    if (ok) {
      toast.success('Conversation copied to clipboard')
    } else {
      toast.error('Copy failed. Select the text and copy it manually.')
    }
  }

  return (
    <div className="flex flex-1 flex-col h-full overflow-hidden bg-background">
      {/* Top bar */}
      <header className="flex h-14 shrink-0 items-center justify-between gap-3 border-b px-6">
        <h1 className="text-base font-semibold">Ask</h1>
        <div className="flex items-center gap-2">
          {headerExtra}
          <Button
            variant="outline"
            size="sm"
            onClick={handleCopyConversation}
            className="h-8 gap-1.5 text-xs"
          >
            <Copy className="size-3.5" />
            <span>Copy conversation</span>
          </Button>
        </div>
      </header>

      {/* Main thread */}
      <main ref={scrollRef} className="flex-1 overflow-y-auto px-6">
        <div className="mx-auto w-full max-w-3xl py-6 flex flex-col gap-6">
          {turns.length === 0 ? (
            <EmptyState dataset={dataset} onSelectSuggestion={onSend} />
          ) : (
            turns.map((turn) => (
              <TurnItem
                key={turn.id}
                turn={turn}
                devMode={devMode}
                onOpenDetails={handleOpenDetails}
                onOpenInspector={(t) => {
                  setInspectTurn(t)
                  setInspectOpen(true)
                }}
                onRetry={onRetry}
                onUpdateTurn={onUpdateTurn}
                sessionId={sessionId}
              />
            ))
          )}
        </div>
      </main>

      {/* Docked composer */}
      <footer className="border-t bg-background px-6 py-4 shrink-0">
        <div className="mx-auto w-full max-w-3xl">
          <Composer onSend={onSend} isPending={isPending} />
        </div>
      </footer>

      {/* Details panel */}
      <DetailsSheet
        turn={activeDetailsTurn}
        open={detailsOpen}
        onOpenChange={setDetailsOpen}
        devMode={devMode}
      />
      <InspectorDialog turn={inspectTurn} open={inspectOpen} onOpenChange={setInspectOpen} />
    </div>
  )
}
