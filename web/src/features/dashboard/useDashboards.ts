import { useCallback, useEffect, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'

import { api } from '@/api/client'
import type { Dashboard } from '@/api/types'
import { notifyFailure } from '@/features/dashboard/useTiles'
import { dashboardKey, readStored, writeStored } from '@/lib/storage'

/**
 * The dashboards of one dataset, and which one is open (remembered per dataset).
 */
export function useDashboards(dataset: string) {
  const queryClient = useQueryClient()
  const key = ['dashboards', dataset] as const
  const query = useQuery({ queryKey: key, queryFn: () => api.dashboards(dataset) })
  const dashboards = query.data?.dashboards ?? []

  const [selectedId, setSelectedId] = useState<string | null>(() => readStored(dashboardKey(dataset)))

  // A dataset switch reads that dataset's remembered tab.
  useEffect(() => {
    setSelectedId(readStored(dashboardKey(dataset)))
  }, [dataset])

  const select = useCallback((id: string) => {
    setSelectedId(id)
    writeStored(dashboardKey(dataset), id)
  }, [dataset])

  // The open dashboard: the remembered one if it still exists, else the first.
  const active: Dashboard | null =
    dashboards.find((d) => d.dashboard_id === selectedId) ?? dashboards[0] ?? null

  const setList = useCallback((update: (list: Dashboard[]) => Dashboard[]) => {
    queryClient.setQueryData<{ dashboards: Dashboard[] }>(['dashboards', dataset], (old) => ({
      dashboards: update(old?.dashboards ?? []),
    }))
  }, [queryClient, dataset])

  const create = useCallback(async (name: string) => {
    try {
      const { dashboard } = await api.createDashboard(name, dataset)
      setList((list) => [...list, dashboard])
      select(dashboard.dashboard_id)
      return dashboard
    } catch (err) {
      notifyFailure(err, 'Failed to create the dashboard.')
      return null
    }
  }, [dataset, select, setList])

  const rename = useCallback(async (id: string, name: string) => {
    try {
      const { dashboard } = await api.renameDashboard(id, name)
      setList((list) => list.map((d) => (d.dashboard_id === id ? dashboard : d)))
      return dashboard
    } catch (err) {
      notifyFailure(err, 'Failed to rename the dashboard.')
      throw err
    }
  }, [setList])

  const remove = useCallback(async (id: string) => {
    try {
      await api.deleteDashboard(id)
      setList((list) => list.filter((d) => d.dashboard_id !== id))
      queryClient.removeQueries({ queryKey: ['tiles', id] })
      void queryClient.invalidateQueries({ queryKey: ['tiles', 'all'] })
    } catch (err) {
      notifyFailure(err, 'Failed to delete the dashboard.')
      throw err
    }
  }, [queryClient, setList])

  return { ...query, dashboards, active, select, create, rename, remove }
}
