import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api } from '@/api/client'
import { AppShell } from '@/features/shell/AppShell'
import { SignInCard } from '@/features/auth/SignInCard'
import { ChatView } from '@/features/chat/ChatView'
import { DashboardView } from '@/features/dashboard/DashboardView'
import { useConversation } from '@/features/chat/useConversation'
import { AUTH_EXPIRED_EVENT, clearToken, getToken } from '@/lib/auth'
import { KEYS, readStored, writeStored } from '@/lib/storage'

function getInitialRoute(): 'chat' | 'dashboard' {
  return window.location.hash === '#/dashboard' ? 'dashboard' : 'chat'
}

function getStoredDevMode(): boolean {
  return readStored(KEYS.devMode) === 'true'
}

export default function App() {
  const queryClient = useQueryClient()
  const [token, setTokenState] = useState<string | null>(getToken)
  const [sessionExpiredMessage, setSessionExpiredMessage] = useState<string | null>(null)
  const isSubmittingSignInRef = useRef(false)

  const [route, setRoute] = useState<'chat' | 'dashboard'>(getInitialRoute)
  const [devMode, setDevModeState] = useState<boolean>(getStoredDevMode)

  // Listen to hash changes
  useEffect(() => {
    const onHashChange = () => {
      setRoute(getInitialRoute())
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const handleNavigate = (nextRoute: 'chat' | 'dashboard') => {
    window.location.hash = `#/${nextRoute}`
    setRoute(nextRoute)
  }

  // Listen for AUTH_EXPIRED_EVENT
  useEffect(() => {
    const handleAuthExpired = () => {
      // If the user is currently submitting the sign-in form, do not overwrite the 401 error.
      if (isSubmittingSignInRef.current) return
      clearToken()
      setTokenState(null)
      setSessionExpiredMessage('Your session expired or the token is invalid. Sign in again.')
    }

    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  }, [])

  // Health query for schemas
  const { data: health } = useQuery({
    queryKey: ['health'],
    queryFn: api.health,
    staleTime: 60_000,
  })

  const availableSchemas = useMemo(() => health?.available_schemas ?? ['public'], [health])
  const defaultSchema = health?.default_schema ?? 'public'

  // Current dataset selection
  const [dataset, setDatasetState] = useState<string>(() => {
    const stored = readStored(KEYS.corpus)
    return stored || 'public'
  })

  // Reconcile dataset once health query resolves
  useEffect(() => {
    if (!health) return
    const stored = readStored(KEYS.corpus)
    if (stored && availableSchemas.includes(stored)) {
      setDatasetState(stored)
    } else {
      const fallback = defaultSchema || availableSchemas[0] || 'public'
      setDatasetState(fallback)
      writeStored(KEYS.corpus, fallback)
    }
  }, [health, availableSchemas, defaultSchema])

  const conversation = useConversation({
    dataset,
    onDatasetChange: (newDataset) => {
      setDatasetState(newDataset)
    },
  })

  // Sessions list query
  const { data: sessionListData } = useQuery({
    queryKey: ['sessions', token],
    queryFn: api.sessions,
    enabled: Boolean(token),
  })

  const sessions = sessionListData?.sessions ?? []

  const handleNewChat = useCallback(() => {
    conversation.newChat()
    if (route !== 'chat') handleNavigate('chat')
  }, [conversation, route])

  const handleSelectDataset = (newDataset: string) => {
    if (newDataset === dataset) return
    writeStored(KEYS.corpus, newDataset)
    setDatasetState(newDataset)
    conversation.newChat()
    if (route !== 'chat') handleNavigate('chat')
    toast.info(`Switched to ${newDataset}. Started a new conversation.`)
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
    if (route !== 'chat') handleNavigate('chat')
  }

  // Not signed in
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

  return (
    <AppShell
      currentRoute={route}
      onNavigate={handleNavigate}
      onNewChat={handleNewChat}
      sessions={sessions}
      activeSessionId={conversation.sessionId}
      onSelectSession={handleSelectSession}
      dataset={dataset}
      availableSchemas={availableSchemas}
      onSelectDataset={handleSelectDataset}
      devMode={devMode}
      onToggleDevMode={handleToggleDevMode}
      onSignOut={handleSignOut}
    >
      {route === 'chat' ? (
        <ChatView
          turns={conversation.turns}
          onSend={conversation.send}
          onRetry={conversation.retry}
          onUpdateTurn={conversation.updateTurn}
          isPending={conversation.isPending}
          dataset={dataset}
          devMode={devMode}
          sessionId={conversation.sessionId}
        />
      ) : (
        <DashboardView devMode={devMode} />
      )}
    </AppShell>
  )
}
