import { useCallback, useEffect, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api } from '@/api/client'
import type { BuildJob } from '@/api/types'
import { notifyFailure } from '@/features/dashboard/useTiles'

const BUILD_POLL_MS = 1500

function isActive(job: BuildJob | null | undefined): boolean {
  return job?.state === 'planning' || job?.state === 'running'
}

/**
 * The AI build of one dashboard: start it, follow it while it runs, and pull
 * each card in as the server adds it (web/DESIGN.md §6).
 */
export function useBuild(dashboardId: string | null) {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['build', dashboardId],
    queryFn: () => api.buildStatus(dashboardId as string),
    enabled: Boolean(dashboardId),
    refetchInterval: (q) => (isActive(q.state.data?.job) ? BUILD_POLL_MS : false),
  })
  const job = query.data?.job ?? null

  // New cards: refetch the dashboard whenever the number of added items grows.
  const added = job?.items.filter((i) => i.state === 'added').length ?? 0
  const seen = useRef<number | null>(null)
  useEffect(() => {
    if (seen.current !== null && added > seen.current && dashboardId) {
      void queryClient.invalidateQueries({ queryKey: ['tiles', dashboardId] })
      void queryClient.invalidateQueries({ queryKey: ['dashboards'] })
    }
    seen.current = added
  }, [added, dashboardId, queryClient])

  // Builds this page watched running. Only those show their result panel and
  // are announced; a build that finished before the page opened stays quiet.
  const [watched, setWatched] = useState<ReadonlySet<string>>(() => new Set())
  useEffect(() => {
    if (job && isActive(job) && !watched.has(job.job_id)) setWatched((w) => new Set(w).add(job.job_id))
  }, [job, watched])

  const announced = useRef<string | null>(null)
  useEffect(() => {
    if (!job || isActive(job) || !job.finished_at || !watched.has(job.job_id)) return
    if (announced.current === job.job_id) return
    announced.current = job.job_id
    if (job.state === 'failed') toast.error(job.error ?? 'The build failed.')
    else toast.success(`Added ${added} of ${job.items.length} insights.`)
  }, [job, added, watched])

  /**
   * Start a build on `targetId`. Explicit, not the hook's own dashboard: a build
   * that first creates a new dashboard must not start on the one that was open.
   */
  const start = useCallback(async (targetId: string, goal?: string) => {
    try {
      const res = await api.buildDashboard(targetId, goal)
      announced.current = null
      setWatched((w) => new Set(w).add(res.job.job_id))
      queryClient.setQueryData(['build', targetId], res)
    } catch (err) {
      notifyFailure(err, 'Could not start the build.')
    }
  }, [queryClient])

  /** The job to show: running, or finished while this page watched it. */
  const visible = job && (isActive(job) || watched.has(job.job_id)) ? job : null
  return { job: visible, active: isActive(job), start }
}
