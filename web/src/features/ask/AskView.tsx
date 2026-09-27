import type { ReactNode } from 'react'

import type { SessionSummary } from '@/api/types'
import { ConversationList } from '@/features/ask/ConversationList'
import { ChatView } from '@/features/chat/ChatView'
import type { useConversation } from '@/features/chat/useConversation'

interface AskViewProps {
  conversation: ReturnType<typeof useConversation>
  sessions: SessionSummary[]
  sessionsLoading: boolean
  onSelectSession: (sessionId: string) => void
  onDeleteSession: (sessionId: string) => Promise<void>
  dataset: string
  devMode: boolean
  datasetPicker: ReactNode
}

/** Ask (web/DESIGN.md §7): conversations on the left, the chat on the right. */
export function AskView({
  conversation,
  sessions,
  sessionsLoading,
  onSelectSession,
  onDeleteSession,
  dataset,
  devMode,
  datasetPicker,
}: AskViewProps) {
  return (
    <div className="flex h-full min-h-0 flex-1 overflow-hidden">
      <ConversationList
        sessions={sessions}
        isLoading={sessionsLoading}
        activeSessionId={conversation.sessionId}
        onSelect={onSelectSession}
        onNewChat={conversation.newChat}
        onDelete={onDeleteSession}
      />
      <ChatView
        turns={conversation.turns}
        onSend={conversation.send}
        onRetry={conversation.retry}
        onUpdateTurn={conversation.updateTurn}
        isPending={conversation.isPending}
        dataset={dataset}
        devMode={devMode}
        sessionId={conversation.sessionId}
        headerExtra={datasetPicker}
      />
    </div>
  )
}
