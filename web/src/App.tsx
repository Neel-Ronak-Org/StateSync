import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MotionConfig } from 'motion/react'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import { AskView } from '@/features/ask/AskView'
import { SignInCard } from '@/features/auth/SignInCard'
import { useConversation } from '@/features/chat/useConversation'
import { DashboardsView } from '@/features/dashboard/DashboardsView'
import { AppShell } from '@/features/shell/AppShell'
import { DatasetPicker } from '@/features/shell/DatasetPicker'
import { useHealth } from '@/hooks/useHealth'
import { AUTH_EXPIRED_EVENT, clearToken, getToken } from '@/lib/auth'
import { hashFor, routeFromHash, type AppRoute } from '@/lib/route'
import { KEYS, readStored, writeStored } from '@/lib/storage'

function getStoredDevMode(): boolean {
  return readStored(KEYS.devMode) === 'true'
}

export default function App() {
  const queryClient = useQueryClient()
  const [token, setTokenState] = useState<string | null>(getToken)
  const [sessionExpiredMessage, setSessionExpiredMessage] = useState<string | null>(null)
  const isSubmittingSignInRef = useRef(false)

  const [route, setRoute] = useState<AppRoute>(() => routeFromHash(window.location.hash))
  const [devMode, setDevModeState] = useState<boolean>(getStoredDevMode)

  useEffect(() => {
    const onHashChange = () => setRoute(routeFromHash(window.location.hash))
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const navigate = useCallback((next: AppRoute) => {
    window.location.hash = hashFor(next)
    setRoute(next)
  }, [])

  useEffect(() => {
    const handleAuthExpired = () => {
      // While the sign-in form is submitting, its own 401 message wins.
      if (isSubmittingSignInRef.current) return
      clearToken()
      setTokenState(null)
      setSessionExpiredMessage('Your session expired or the token is invalid. Sign in again.')
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  }, [])

  const { data: health } = useHealth()
  const availableSchemas = useMemo(() => health?.available_schemas ?? ['public'], [health])
  const liveSchemas = useMemo(() => health?.live_schemas ?? [], [health])

  // The dataset: the stored choice if it still exists; a new browser starts on
  // the live demo data when there is one (web/DESIGN.md §4).
  const [dataset, setDatasetState] = useState<string>(() => readStored(KEYS.corpus) || 'public')
  useEffect(() => {
    if (!health) return
    const stored = readStored(KEYS.corpus)
    if (stored && availableSchemas.includes(stored)) {
      setDatasetState(stored)
      return
    }
    const preferred = liveSchemas.find((s) => availableSchemas.includes(s)) ?? health.default_schema ?? availableSchemas[0]
    setDatasetState(preferred)
    writeStored(KEYS.corpus, preferred)
  }, [health, availableSchemas, liveSchemas])

  const conversation = useConversation({ dataset, onDatasetChange: setDatasetState })

  const sessionsQuery = useQuery({
    queryKey: ['sessions', token],
    queryFn: () => api.sessions(),
    enabled: Boolean(token),
  })
  const sessions = sessionsQuery.data?.sessions ?? []

  const handleSelectDataset = (next: string) => {
    if (next === dataset) return
    writeStored(KEYS.corpus, next)
    setDatasetState(next)
    // A conversation stays on the dataset it started on (migration 018).
    conversation.newChat()
    if (route === 'ask') toast.info(`Switched to ${next}. Started a new conversation.`)
  }

  const handleToggleDevMode = (enabled: boolean) => {
    setDevModeState(enabled)
    writeStored(KEYS.devMode, enabled ? 'true' : 'false')
  }

  const handleSignOut = () => {
    clearToken()
    setTokenState(null)
    setSessionExpiredMessage(null)
  }

  const handleSelectSession = async (selectedId: string) => {
    await conversation.openSession(selectedId)
    if (route !== 'ask') navigate('ask')
  }

  const handleDeleteSession = async (sessionId: string) => {
    try {
      await api.deleteSession(sessionId)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to delete the conversation.')
      throw err
    }
    if (sessionId === conversation.sessionId) conversation.newChat()
    await queryClient.invalidateQueries({ queryKey: ['sessions'] })
    toast.success('Conversation deleted')
  }

  if (!token) {
    return (
      <SignInCard
        onSuccess={() => {
          setTokenState(getToken())
          setSessionExpiredMessage(null)
          queryClient.invalidateQueries()
        }}
        sessionExpiredMessage={sessionExpiredMessage}
        onAttemptStart={() => {
          isSubmittingSignInRef.current = true
        }}
        onAttemptEnd={() => {
          isSubmittingSignInRef.current = false
        }}
      />
    )
  }

  const picker = (
    <DatasetPicker dataset={dataset} available={availableSchemas} live={liveSchemas} onChange={handleSelectDataset} />
  )

  return (
    <MotionConfig reducedMotion="user">
      <AppShell
        route={route}
        onNavigate={navigate}
        devMode={devMode}
        onToggleDevMode={handleToggleDevMode}
        onSignOut={handleSignOut}
      >
        {route === 'dashboards' ? (
          <DashboardsView
            dataset={dataset}
            availableSchemas={availableSchemas}
            liveSchemas={liveSchemas}
            onChangeDataset={handleSelectDataset}
            devMode={devMode}
            onAsk={() => navigate('ask')}
          />
        ) : (
          <AskView
            conversation={conversation}
            sessions={sessions}
            sessionsLoading={sessionsQuery.isLoading}
            onSelectSession={(id) => void handleSelectSession(id)}
            onDeleteSession={handleDeleteSession}
            dataset={dataset}
            devMode={devMode}
            datasetPicker={picker}
          />
        )}
      </AppShell>
    </MotionConfig>
  )
}
