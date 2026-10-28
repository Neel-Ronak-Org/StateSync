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
import { WorkspacePicker } from '@/features/shell/WorkspacePicker'
import { useWorkspaces } from '@/features/workspaces/useWorkspaces'
import { WorkspaceDetail } from '@/features/workspaces/WorkspaceDetail'
import { WorkspacesView } from '@/features/workspaces/WorkspacesView'
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

  // Every question is asked in a workspace. The stored one is restored if it still exists (only once the
  // list has been read: a failed fetch is not a deletion); otherwise the first one is chosen.
  useEffect(() => {
    if (!workspacesLoaded) return
    if (activeWorkspaceId && activeWorkspace) return
    const next = workspaces[0]?.workspace_id ?? null
    setActiveWorkspaceId(next)
    writeStored(KEYS.workspace, next)
  }, [workspacesLoaded, activeWorkspaceId, activeWorkspace, workspaces])

  const dataset = activeWorkspace?.target_schema ?? ''

  // Opening a past conversation moves to the workspace on its database: a workspace answers only there.
  const handleConversationDataset = useCallback((next: string) => {
    if (activeWorkspace && next === activeWorkspace.target_schema) return
    const ws = workspaces.find((w) => w.target_schema === next)
    if (ws) {
      setActiveWorkspaceId(ws.workspace_id)
      writeStored(KEYS.workspace, ws.workspace_id)
    }
  }, [activeWorkspace, workspaces])

  const conversation = useConversation({
    dataset,
    workspaceId: activeWorkspaceId,
    onDatasetChange: handleConversationDataset,
  })

  const sessionsQuery = useQuery({
    queryKey: ['sessions', signedIn],
    queryFn: () => api.sessions(),
    enabled: signedIn,
  })
  const sessions = sessionsQuery.data?.sessions ?? []

  const handleSelectWorkspace = (wsId: string) => {
    const ws = workspaces.find((w) => w.workspace_id === wsId)
    if (!ws) return
    if (wsId === activeWorkspaceId) return
    setActiveWorkspaceId(wsId)
    writeStored(KEYS.workspace, wsId)
    conversation.newChat()
    if (route === 'ask') toast.info(`Switched to ${ws.name}. Started a new conversation.`)
  }

  const handleAskInWorkspace = (wsId: string) => {
    const ws = workspaces.find((w) => w.workspace_id === wsId)
    setActiveWorkspaceId(wsId)
    writeStored(KEYS.workspace, wsId)
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
    <WorkspacePicker
      workspaces={workspaces}
      activeWorkspaceId={activeWorkspace?.workspace_id ?? null}
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
            dataset={dataset}
            workspaceName={activeWorkspace?.name ?? ''}
            picker={picker}
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
            dataset={dataset}
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
              onCreateWorkspace={createWorkspace}
              onCreated={handleWorkspaceCreated}
            />
          ))}
      </AppShell>
    </MotionConfig>
  )
}
