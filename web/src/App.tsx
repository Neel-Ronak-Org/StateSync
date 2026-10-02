import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { MotionConfig } from 'motion/react'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { Workspace } from '@/api/types'
import { AskView } from '@/features/ask/AskView'
import { SignInCard } from '@/features/auth/SignInCard'
import { useConversation } from '@/features/chat/useConversation'
import { LibraryView } from '@/features/library/LibraryView'
import { DashboardsView } from '@/features/dashboard/DashboardsView'
import { AppShell } from '@/features/shell/AppShell'
import { DatasetPicker } from '@/features/shell/DatasetPicker'
import { useWorkspaces } from '@/features/workspaces/useWorkspaces'
import { WorkspaceDetail } from '@/features/workspaces/WorkspaceDetail'
import { WorkspacesView } from '@/features/workspaces/WorkspacesView'
import { useHealth } from '@/hooks/useHealth'
import { AUTH_EXPIRED_EVENT, clearSignedIn, isSignedIn, markSignedIn, takeLegacyToken } from '@/lib/auth'
import { hashFor, routeFromHash, workspaceIdFromHash, type AppRoute } from '@/lib/route'
import { KEYS, readStored, writeStored } from '@/lib/storage'

function getStoredDevMode(): boolean {
  return readStored(KEYS.devMode) === 'true'
}

export default function App() {
  const queryClient = useQueryClient()
  const [signedIn, setSignedIn] = useState<boolean>(isSignedIn)
  // A token an older version left in localStorage: exchanged for a session cookie once, then gone.
  const [legacyToken] = useState<string | null>(takeLegacyToken)
  const [migrating, setMigrating] = useState<boolean>(Boolean(legacyToken))
  const [sessionExpiredMessage, setSessionExpiredMessage] = useState<string | null>(null)
  const isSubmittingSignInRef = useRef(false)

  const [hash, setHash] = useState(() => window.location.hash)
  const [route, setRoute] = useState<AppRoute>(() => routeFromHash(window.location.hash))
  const [devMode, setDevModeState] = useState<boolean>(getStoredDevMode)

  useEffect(() => {
    const onHashChange = () => {
      setHash(window.location.hash)
      setRoute(routeFromHash(window.location.hash))
    }
    window.addEventListener('hashchange', onHashChange)
    return () => window.removeEventListener('hashchange', onHashChange)
  }, [])

  const currentWorkspaceId = useMemo(() => {
    if (route !== 'workspaces') return null
    return workspaceIdFromHash(hash)
  }, [route, hash])

  const navigate = useCallback((next: AppRoute) => {
    window.location.hash = hashFor(next)
    setRoute(next)
    setHash(hashFor(next))
  }, [])

  useEffect(() => {
    const handleAuthExpired = () => {
      // While the sign-in form is submitting, its own 401 message wins.
      if (isSubmittingSignInRef.current) return
      clearSignedIn()
      setSignedIn(false)
      setSessionExpiredMessage('Your session expired or the token is invalid. Sign in again.')
    }
    window.addEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
    return () => window.removeEventListener(AUTH_EXPIRED_EVENT, handleAuthExpired)
  }, [])

  useEffect(() => {
    if (!legacyToken) return
    api
      .signIn(legacyToken)
      .then((session) => {
        markSignedIn(session.principal_id)
        setSignedIn(true)
      })
      .catch(() => {
        clearSignedIn()
        setSignedIn(false)
      })
      .finally(() => setMigrating(false))
  }, [legacyToken])

  const { data: health } = useHealth()
  const availableSchemas = useMemo(() => health?.available_schemas ?? ['public'], [health])
  const liveSchemas = useMemo(() => health?.live_schemas ?? [], [health])

  const {
    workspaces,
    createWorkspace,
    deleteWorkspace,
    isLoading: workspacesLoading,
    isLoaded: workspacesLoaded,
  } = useWorkspaces(signedIn)

  const [activeWorkspaceId, setActiveWorkspaceId] = useState<string | null>(() =>
    readStored(KEYS.workspace),
  )

  const activeWorkspace = useMemo(
    () => workspaces.find((w) => w.workspace_id === activeWorkspaceId) ?? null,
    [workspaces, activeWorkspaceId],
  )

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
    const preferred =
      liveSchemas.find((s) => availableSchemas.includes(s)) ??
      health.default_schema ??
      availableSchemas[0]
    setDatasetState(preferred)
    writeStored(KEYS.corpus, preferred)
  }, [health, availableSchemas, liveSchemas])

  // On reload the stored workspace is restored if it still exists in the list
  // (only once the list has actually been read: a failed fetch is not a deletion).
  useEffect(() => {
    if (!workspacesLoaded) return
    if (activeWorkspaceId) {
      if (activeWorkspace) {
        if (dataset !== activeWorkspace.target_schema) {
          setDatasetState(activeWorkspace.target_schema)
          writeStored(KEYS.corpus, activeWorkspace.target_schema)
        }
      } else {
        setActiveWorkspaceId(null)
        writeStored(KEYS.workspace, null)
      }
    }
  }, [workspacesLoaded, activeWorkspaceId, activeWorkspace, dataset])

  const effectiveDataset = activeWorkspace ? activeWorkspace.target_schema : dataset

  // Opening a past conversation on another dataset leaves the workspace:
  // a workspace answers only on its own dataset.
  const handleConversationDataset = useCallback((next: string) => {
    if (activeWorkspace && next !== activeWorkspace.target_schema) {
      setActiveWorkspaceId(null)
      writeStored(KEYS.workspace, null)
    }
    writeStored(KEYS.corpus, next)
    setDatasetState(next)
  }, [activeWorkspace])

  const conversation = useConversation({
    dataset: effectiveDataset,
    workspaceId: activeWorkspaceId,
    onDatasetChange: handleConversationDataset,
  })

  const sessionsQuery = useQuery({
    queryKey: ['sessions', signedIn],
    queryFn: () => api.sessions(),
    enabled: signedIn,
  })
  const sessions = sessionsQuery.data?.sessions ?? []

  const handleSelectDataset = (next: string) => {
    if (next === dataset && !activeWorkspaceId) return
    setActiveWorkspaceId(null)
    writeStored(KEYS.workspace, null)
    writeStored(KEYS.corpus, next)
    setDatasetState(next)
    // A conversation stays on the dataset it started on (migration 018).
    conversation.newChat()
    if (route === 'ask') toast.info(`Switched to ${next}. Started a new conversation.`)
  }

  const handleSelectWorkspace = (wsId: string) => {
    const ws = workspaces.find((w) => w.workspace_id === wsId)
    if (!ws) return
    if (wsId === activeWorkspaceId) return
    setActiveWorkspaceId(wsId)
    writeStored(KEYS.workspace, wsId)
    writeStored(KEYS.corpus, ws.target_schema)
    setDatasetState(ws.target_schema)
    conversation.newChat()
    if (route === 'ask') toast.info(`Switched to ${ws.name}. Started a new conversation.`)
  }

  const handleAskInWorkspace = (wsId: string, targetSchema: string) => {
    const ws = workspaces.find((w) => w.workspace_id === wsId)
    setActiveWorkspaceId(wsId)
    writeStored(KEYS.workspace, wsId)
    writeStored(KEYS.corpus, targetSchema)
    setDatasetState(targetSchema)
    conversation.newChat()
    if (ws) toast.info(`Switched to ${ws.name}. Started a new conversation.`)
    navigate('ask')
  }

  const handleDeleteWorkspace = async (wsId: string) => {
    await deleteWorkspace(wsId)
    if (wsId === activeWorkspaceId) {
      setActiveWorkspaceId(null)
      writeStored(KEYS.workspace, null)
    }
  }

  const handleWorkspaceCreated = (newWs: Workspace) => {
    window.location.hash = `#/workspaces/${newWs.workspace_id}`
  }

  const handleToggleDevMode = (enabled: boolean) => {
    setDevModeState(enabled)
    writeStored(KEYS.devMode, enabled ? 'true' : 'false')
  }

  const handleSignOut = () => {
    // Clear the HttpOnly cookie server-side; signed out locally either way.
    void api.signOut().catch(() => undefined)
    clearSignedIn()
    setSignedIn(false)
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

  if (migrating) return null

  if (!signedIn) {
    return (
      <SignInCard
        onSuccess={() => {
          setSignedIn(true)
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
    <DatasetPicker
      dataset={effectiveDataset}
      available={availableSchemas}
      live={liveSchemas}
      workspaces={workspaces}
      activeWorkspaceId={activeWorkspace?.workspace_id ?? null}
      onSelectDataset={handleSelectDataset}
      onSelectWorkspace={handleSelectWorkspace}
    />
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
        {route === 'dashboards' && (
          <DashboardsView
            dataset={effectiveDataset}
            availableSchemas={availableSchemas}
            liveSchemas={liveSchemas}
            workspaces={workspaces}
            activeWorkspaceId={activeWorkspace?.workspace_id ?? null}
            onChangeDataset={handleSelectDataset}
            onSelectWorkspace={handleSelectWorkspace}
            devMode={devMode}
            onAsk={() => navigate('ask')}
          />
        )}
        {route === 'ask' && (
          <AskView
            conversation={conversation}
            sessions={sessions}
            sessionsLoading={sessionsQuery.isLoading}
            onSelectSession={(id) => void handleSelectSession(id)}
            onDeleteSession={handleDeleteSession}
            dataset={effectiveDataset}
            devMode={devMode}
            datasetPicker={picker}
          />
        )}
        {route === 'library' && <LibraryView />}
        {route === 'workspaces' &&
          (currentWorkspaceId ? (
            <WorkspaceDetail
              key={currentWorkspaceId}
              workspaceId={currentWorkspaceId}
              onAskInWorkspace={handleAskInWorkspace}
              onDeleteWorkspace={handleDeleteWorkspace}
            />
          ) : (
            <WorkspacesView
              workspaces={workspaces}
              isLoading={workspacesLoading}
              availableSchemas={availableSchemas}
              onCreateWorkspace={createWorkspace}
              onCreated={handleWorkspaceCreated}
            />
          ))}
      </AppShell>
    </MotionConfig>
  )
}
