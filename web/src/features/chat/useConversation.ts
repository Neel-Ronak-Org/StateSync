import { useCallback, useState } from 'react'
import { useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { QueryResponse, SessionList } from '@/api/types'
import { KEYS, readJson, readStored, writeJson, writeStored } from '@/lib/storage'
import type { ChatTurn } from '@/types/chat'

interface UseConversationOptions {
  dataset: string
  workspaceId?: string | null
  onDatasetChange: (newDataset: string) => void
}

function getOrCreateSessionId(): string {
  let id = readStored(KEYS.session, 'session')
  if (!id) {
    id = crypto.randomUUID()
    writeStored(KEYS.session, id, 'session')
  }
  return id
}

function mapResponseToTurn(
  res: QueryResponse,
  baseTurn: Partial<ChatTurn>,
  currentDataset: string,
): Partial<ChatTurn> {
  return {
    answer: res.answer_text,
    chart: res.chart,
    rows: res.state_trace.sql_rows ?? [],
    columns: res.state_trace.sql_columns ?? [],
    sql: res.state_trace.executed_sql,
    route: res.state_trace.route,
    turnIndex: res.state_trace.turn_index ?? baseTurn.turnIndex ?? 1,
    sqlRowsCount: res.state_trace.sql_rows_count ?? (res.state_trace.sql_rows?.length ?? 0),
    dataset: res.state_trace.target_schema || currentDataset,
    citations: res.citations ?? [],
    latencyMs: res.latency_ms,
    stateTrace: res.state_trace,
    verifiedExample: Boolean(res.state_trace.verified_example),
    status: 'done',
    error: null,
  }
}

export function useConversation({ dataset, workspaceId, onDatasetChange }: UseConversationOptions) {
  const queryClient = useQueryClient()
  const [sessionId, setSessionId] = useState<string>(getOrCreateSessionId)
  const [turns, setTurns] = useState<ChatTurn[]>(() =>
    readJson<ChatTurn[]>(KEYS.thread, [], 'session'),
  )
  const [isPending, setIsPending] = useState(false)

  // Sync turns to sessionStorage, capping rows per turn at 200 to stay under quota
  const persistTurns = useCallback((updatedTurns: ChatTurn[]) => {
    // The trace's own copy of the rows is dropped (the turn keeps up to 200 in `rows`): with it, a long
    // conversation outgrew sessionStorage, the write failed silently and a reload lost the Inspector.
    const capped = updatedTurns.map((t) => ({
      ...t,
      rows: t.rows ? t.rows.slice(0, 200) : null,
      stateTrace: t.stateTrace ? { ...t.stateTrace, sql_rows: [] } : t.stateTrace,
    }))
    writeJson(KEYS.thread, capped, 'session')
  }, [])

  const updateTurn = useCallback(
    (turnId: string, updates: Partial<ChatTurn>) => {
      setTurns((prev) => {
        const next = prev.map((t) => (t.id === turnId ? { ...t, ...updates } : t))
        persistTurns(next)
        return next
      })
    },
    [persistTurns],
  )

  const newChat = useCallback(() => {
    const newSessionId = crypto.randomUUID()
    writeStored(KEYS.session, newSessionId, 'session')
    writeJson(KEYS.thread, [], 'session')
    setSessionId(newSessionId)
    setTurns([])
  }, [])

  // Single code path that executes a query and maps the response to a turn
  const executeQueryForTurn = useCallback(
    async (turnId: string, question: string, currentSessionId: string, currentDataset: string) => {
      setIsPending(true)
      try {
        const res = await api.query({
          query: question,
          session_id: currentSessionId,
          workspace_id: workspaceId ?? undefined,
        })

        setTurns((prev) => {
          const updated = prev.map((t) =>
            t.id === turnId ? ({ ...t, ...mapResponseToTurn(res, t, currentDataset) } as ChatTurn) : t,
          )
          persistTurns(updated)
          return updated
        })

        queryClient.invalidateQueries({ queryKey: ['sessions'] })
      } catch (err) {
        const errorMsg =
          err instanceof ApiError ? err.message : 'Can not reach the server. Try again shortly.'
        setTurns((prev) => {
          const updated = prev.map((t) =>
            t.id === turnId ? { ...t, status: 'error' as const, error: errorMsg } : t,
          )
          persistTurns(updated)
          return updated
        })
      } finally {
        setIsPending(false)
      }
    },
    [persistTurns, queryClient, workspaceId],
  )

  const send = useCallback(
    async (question: string) => {
      const turnId = `turn-${Date.now()}`
      const pendingTurn: ChatTurn = {
        id: turnId,
        question,
        answer: '',
        chart: null,
        rows: null,
        columns: [],
        sql: null,
        route: null,
        turnIndex: turns.length + 1,
        sqlRowsCount: 0,
        dataset,
        citations: [],
        latencyMs: 0,
        status: 'pending',
      }

      const nextTurns = [...turns, pendingTurn]
      setTurns(nextTurns)
      persistTurns(nextTurns)

      await executeQueryForTurn(turnId, question, sessionId, dataset)
    },
    [dataset, executeQueryForTurn, persistTurns, sessionId, turns],
  )

  const retry = useCallback(
    async (failedTurn: ChatTurn) => {
      updateTurn(failedTurn.id, { status: 'pending', error: null })
      await executeQueryForTurn(failedTurn.id, failedTurn.question, sessionId, dataset)
    },
    [dataset, executeQueryForTurn, sessionId, updateTurn],
  )

  const openSession = useCallback(
    async (selectedId: string) => {
      try {
        const detail = await api.session(selectedId)

        // Find session summary for target_schema
        const sessionSummary = queryClient
          .getQueriesData<SessionList>({ queryKey: ['sessions'] })
          .flatMap(([, d]) => d?.sessions ?? [])
          .find((s) => s.session_id === selectedId)
        const sessionTargetSchema =
          sessionSummary?.target_schema ??
          detail.turns.find((t) => t.target_schema)?.target_schema ??
          null

        // If session dataset differs from current dataset, switch to it without clearing thread
        let effectiveDataset = dataset
        if (sessionTargetSchema && sessionTargetSchema !== dataset) {
          writeStored(KEYS.corpus, sessionTargetSchema)
          onDatasetChange(sessionTargetSchema)
          effectiveDataset = sessionTargetSchema
          toast.info(`Opened a conversation on ${sessionTargetSchema}.`)
        }

        const replayed: ChatTurn[] = detail.turns.map((t) => ({
          id: `turn-${t.turn_index}`,
          question: t.raw_query,
          answer: t.answer_text,
          chart: t.chart,
          rows: t.sql_rows_preview ?? [],
          columns: t.sql_columns ?? [],
          sql: t.executed_sql,
          route: t.route,
          turnIndex: t.turn_index,
          sqlRowsCount: t.sql_rows_count ?? (t.sql_rows_preview?.length ?? 0),
          dataset: t.target_schema ?? sessionTargetSchema ?? effectiveDataset,
          // The stored trace and citations (migration 028) bring back the Inspector and the source chips.
          citations: t.detail?.citations ?? [],
          latencyMs: t.detail?.latency_ms ?? 0,
          stateTrace: t.detail?.state_trace ? { ...t.detail.state_trace, turn_index: t.turn_index } : undefined,
          verifiedExample: Boolean(t.detail?.state_trace?.verified_example),
          status: 'done',
        }))

        setSessionId(selectedId)
        writeStored(KEYS.session, selectedId, 'session')
        setTurns(replayed)
        persistTurns(replayed)
      } catch (err) {
        console.error('Failed to load session', err)
        toast.error('Failed to load conversation.')
      }
    },
    [dataset, onDatasetChange, persistTurns, queryClient],
  )

  return {
    turns,
    sessionId,
    isPending,
    send,
    retry,
    newChat,
    openSession,
    updateTurn,
  }
}
