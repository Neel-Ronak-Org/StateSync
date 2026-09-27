import { useEffect, useRef } from 'react'
import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { SetupJob, SetupRun } from '@/api/types'

const DRAFT_POLL_MS = 1500
const SETUP_POLL_MS = 2000

/** My workspaces (only fetched when signed in), with create and delete. */
export function useWorkspaces(enabled = true) {
  const queryClient = useQueryClient()

  const listQuery = useQuery({
    queryKey: ['workspaces'],
    queryFn: () => api.workspaces(),
    staleTime: 10_000,
    enabled,
  })

  const createMutation = useMutation({
    mutationFn: (body: { name: string; target_schema: string; business_context?: string }) =>
      api.createWorkspace(body),
    onSuccess: (res) => {
      queryClient.setQueryData(['workspace', res.workspace.workspace_id], { workspace: res.workspace })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (workspaceId: string) => api.deleteWorkspace(workspaceId),
    onSuccess: (_, workspaceId) => {
      queryClient.removeQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  return {
    workspaces: listQuery.data?.workspaces ?? [],
    isLoading: listQuery.isLoading,
    /** True once the list has been read; before that a missing workspace is unknown, not deleted. */
    isLoaded: listQuery.isSuccess,
    error: listQuery.error,
    createWorkspace: createMutation.mutateAsync,
    isCreating: createMutation.isPending,
    createError: createMutation.error,
    deleteWorkspace: deleteMutation.mutateAsync,
    isDeleting: deleteMutation.isPending,
    refetch: listQuery.refetch,
  }
}

/** Hook for a single workspace's details and metadata updates. */
export function useWorkspace(workspaceId: string | null) {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['workspace', workspaceId],
    queryFn: () => api.workspace(workspaceId!),
    enabled: Boolean(workspaceId),
    retry: false,
  })

  const updateMutation = useMutation({
    mutationFn: (body: { name?: string; business_context?: string }) =>
      api.updateWorkspace(workspaceId!, body),
    onSuccess: (res) => {
      queryClient.setQueryData(['workspace', workspaceId], res)
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  return {
    workspace: query.data?.workspace ?? null,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    updateWorkspace: updateMutation.mutateAsync,
    isUpdating: updateMutation.isPending,
    refetch: query.refetch,
  }
}

/** Hook for the workspace column catalog and description updates. */
export function useCatalog(workspaceId: string | null) {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['workspace-catalog', workspaceId],
    queryFn: () => api.workspaceCatalog(workspaceId!),
    enabled: Boolean(workspaceId),
  })

  const updateMutation = useMutation({
    mutationFn: (items: { table_name: string; column_name: string; description: string | null }[]) =>
      api.updateDescriptions(workspaceId!, items),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-catalog', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  return {
    catalog: query.data?.columns ?? [],
    isLoading: query.isLoading,
    updateDescriptions: updateMutation.mutateAsync,
    isSaving: updateMutation.isPending,
    refetch: query.refetch,
  }
}

/** Hook for workspace definitions (add, delete, list). */
export function useDefinitions(workspaceId: string | null) {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['workspace-definitions', workspaceId],
    queryFn: () => api.workspaceDefinitions(workspaceId!),
    enabled: Boolean(workspaceId),
  })

  const addMutation = useMutation({
    mutationFn: (items: { term: string; definition: string }[]) =>
      api.updateDefinitions(workspaceId!, items),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-definitions', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  const deleteMutation = useMutation({
    mutationFn: (definitionId: string) => api.deleteDefinition(workspaceId!, definitionId),
    onSuccess: () => {
      void queryClient.invalidateQueries({ queryKey: ['workspace-definitions', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  return {
    definitions: query.data?.definitions ?? [],
    isLoading: query.isLoading,
    addDefinitions: addMutation.mutateAsync,
    isAdding: addMutation.isPending,
    deleteDefinition: deleteMutation.mutateAsync,
    isDeleting: deleteMutation.isPending,
    refetch: query.refetch,
  }
}

/** Hook for drafting descriptions with AI, including polling. */
export function useDraftStatus(workspaceId: string | null) {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['workspace-draft', workspaceId],
    queryFn: () => api.draftStatus(workspaceId!),
    enabled: Boolean(workspaceId),
    refetchInterval: (q) => (q.state.data?.job?.state === 'running' ? DRAFT_POLL_MS : false),
  })

  const job = query.data?.job ?? null
  const prevJobState = useRef<string | null>(null)

  useEffect(() => {
    if (prevJobState.current === 'running' && job && (job.state === 'done' || job.state === 'failed')) {
      void queryClient.invalidateQueries({ queryKey: ['workspace-catalog', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace-definitions', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    }
    prevJobState.current = job?.state ?? null
  }, [job, workspaceId, queryClient])

  const startMutation = useMutation({
    mutationFn: () => api.startDraft(workspaceId!),
    onSuccess: (res) => {
      queryClient.setQueryData(['workspace-draft', workspaceId], { job: res.job })
      void query.refetch()
    },
  })

  return {
    job,
    isLoading: query.isLoading,
    startDraft: startMutation.mutateAsync,
    isStarting: startMutation.isPending,
    refetch: query.refetch,
  }
}

/** Hook for running and grading the setup test. */
export function useSetupStatus(workspaceId: string | null) {
  const queryClient = useQueryClient()

  const query = useQuery({
    queryKey: ['workspace-setup', workspaceId],
    queryFn: () => api.setupStatus(workspaceId!),
    enabled: Boolean(workspaceId),
    refetchInterval: (q) => {
      const d = q.state.data
      const isRunning = d?.job?.state === 'running' || d?.run?.state === 'running'
      return isRunning ? SETUP_POLL_MS : false
    },
  })

  const job = query.data?.job ?? null
  const run = query.data?.run ?? null
  const isRunning = job?.state === 'running' || run?.state === 'running'
  const prevRunning = useRef<boolean>(false)

  useEffect(() => {
    if (prevRunning.current && !isRunning) {
      void queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    }
    prevRunning.current = isRunning
  }, [isRunning, workspaceId, queryClient])

  const startMutation = useMutation({
    mutationFn: () => api.startSetup(workspaceId!),
    onSuccess: () => {
      void query.refetch()
    },
  })

  const setVerdictMutation = useMutation({
    mutationFn: ({
      runId,
      position,
      verdict,
    }: {
      runId: string
      position: number
      verdict: 'right' | 'wrong'
    }) => api.setResultVerdict(workspaceId!, runId, position, verdict),
    onSuccess: (res) => {
      queryClient.setQueryData(
        ['workspace-setup', workspaceId],
        (old: { run: SetupRun | null; job: SetupJob | null } | undefined) => ({
          job: old?.job ?? null,
          run: res.run,
        }),
      )
      void queryClient.invalidateQueries({ queryKey: ['workspace', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  return {
    job,
    run,
    isRunning,
    startSetup: startMutation.mutateAsync,
    isStarting: startMutation.isPending,
    setVerdict: (runId: string, position: number, verdict: 'right' | 'wrong') =>
      setVerdictMutation.mutateAsync({ runId, position, verdict }),
    isSettingVerdict: setVerdictMutation.isPending,
    refetch: query.refetch,
  }
}

/** Hook for approving workspace knowledge. */
export function useApproveWorkspace(workspaceId: string | null) {
  const queryClient = useQueryClient()

  const approveMutation = useMutation({
    mutationFn: () => api.approveWorkspace(workspaceId!),
    onSuccess: (res) => {
      queryClient.setQueryData(['workspace', workspaceId], { workspace: res.workspace })
      void queryClient.invalidateQueries({ queryKey: ['workspace-catalog', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspace-definitions', workspaceId] })
      void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
    },
  })

  return {
    approve: approveMutation.mutateAsync,
    isApproving: approveMutation.isPending,
  }
}
