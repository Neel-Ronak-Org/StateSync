import { useQuery } from '@tanstack/react-query'

import { api } from '@/api/client'

/**
 * The orchestrator's /health, shared by the dataset picker and the connection
 * light. Polled every 30 s so a stopped stack or a missing model shows within
 * half a minute, not only when the next question fails.
 */
export function useHealth() {
  return useQuery({
    queryKey: ['health'],
    queryFn: () => api.health(),
    refetchInterval: 30_000,
    staleTime: 15_000,
    retry: false,
  })
}
