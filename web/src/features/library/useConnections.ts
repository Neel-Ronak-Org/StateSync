import { useCallback } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import { datasetLabel, isConnectionId } from '@/features/library/datasets'

const KEY = ['library-connections'] as const

/**
 * My connected databases. The password is never in here: creating a connection
 * calls the API directly (see AddDatabaseDialog), so it never enters a query or
 * mutation cache.
 */
export function useConnections(enabled = true) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: KEY,
    queryFn: () => api.connections(),
    staleTime: 10_000,
    enabled,
  })
  const refresh = useCallback(() => {
    void queryClient.invalidateQueries({ queryKey: KEY })
    // A connection shows in a workspace's dataset, so workspace lists may change too.
    void queryClient.invalidateQueries({ queryKey: ['workspaces'] })
  }, [queryClient])
  return {
    connections: query.data?.connections ?? [],
    available: query.data?.available ?? true,
    isLoading: query.isLoading,
    isError: query.isError,
    error: query.error,
    refetch: query.refetch,
    refresh,
  }
}

/**
 * A function that turns a dataset (or session/turn target_schema) into words.
 * The list is only fetched when one of `datasets` is a connection id, so pages
 * that show the sample datasets make no extra request.
 */
export function useDatasetLabel(datasets: readonly (string | null | undefined)[]): (dataset: string) => string {
  const needed = datasets.some((d) => isConnectionId(d))
  const query = useQuery({
    queryKey: KEY,
    queryFn: () => api.connections(),
    staleTime: 10_000,
    enabled: needed,
  })
  const list = query.data?.connections
  return useCallback((dataset: string) => datasetLabel(dataset, list), [list])
}
