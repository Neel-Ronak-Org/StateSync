import { useCallback, useEffect, useMemo, useRef, useState } from 'react'
import { useQuery, useQueryClient } from '@tanstack/react-query'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { LayoutItem, Tile, UpdateTileRequest } from '@/api/types'

/** How often an open dashboard asks for fresh tiles; the server re-runs the ones that are due. */
export const POLL_MS = 15_000

/**
 * Tell the user a tile action failed. ApiError messages are written to be shown
 * (the server's plain `detail`, or client.ts's fixed sentences); anything else is
 * a programming error, so the user gets a fixed sentence, not its raw text.
 */
export function notifyFailure(err: unknown, fallback: string): void {
  toast.error(err instanceof ApiError ? err.message : fallback)
}

const tilesKey = (dashboardId: string | null) => ['tiles', dashboardId ?? 'none'] as const

function rowsSignature(tile: Tile): string {
  return JSON.stringify(tile.result?.rows ?? null)
}

/**
 * The tiles of one dashboard, polled while it is open (web/DESIGN.md §6).
 * `changed` holds a counter per tile that goes up when a poll brings DIFFERENT
 * rows (not merely a newer timestamp), which is what makes a card flash.
 */
export function useTiles(dashboardId: string | null) {
  const queryClient = useQueryClient()
  const key = useMemo(() => tilesKey(dashboardId), [dashboardId])
  const query = useQuery({
    queryKey: key,
    queryFn: () => api.tiles(dashboardId ?? undefined),
    enabled: Boolean(dashboardId),
    refetchInterval: POLL_MS,
    refetchIntervalInBackground: false,
  })

  const [refreshingTileIds, setRefreshingTileIds] = useState<Set<string>>(new Set())
  const [isRefreshingAll, setIsRefreshingAll] = useState(false)
  const [changed, setChanged] = useState<Record<string, number>>({})
  const signatures = useRef<Map<string, string>>(new Map())

  // Compare each poll's rows with the last seen ones.
  useEffect(() => {
    const tiles = query.data?.tiles
    if (!tiles) return
    const bumped: string[] = []
    for (const tile of tiles) {
      const signature = rowsSignature(tile)
      const previous = signatures.current.get(tile.tile_id)
      if (previous !== undefined && previous !== signature) bumped.push(tile.tile_id)
      signatures.current.set(tile.tile_id, signature)
    }
    if (bumped.length) {
      setChanged((prev) => {
        const next = { ...prev }
        for (const id of bumped) next[id] = (next[id] ?? 0) + 1
        return next
      })
    }
  }, [query.data])

  const setTileInCache = useCallback((updated: Tile) => {
    queryClient.setQueryData<{ tiles: Tile[] }>(key, (old) => {
      const tiles = old?.tiles ?? []
      if (updated.dashboard_id !== dashboardId) {
        return { tiles: tiles.filter((t) => t.tile_id !== updated.tile_id) }
      }
      const idx = tiles.findIndex((t) => t.tile_id === updated.tile_id)
      if (idx < 0) return { tiles: [...tiles, updated] }
      const next = [...tiles]
      next[idx] = updated
      return { tiles: next }
    })
    // The chat's "already pinned?" check reads every tile.
    void queryClient.invalidateQueries({ queryKey: ['tiles', 'all'] })
  }, [queryClient, key, dashboardId])

  const withRefreshing = useCallback(async <T,>(tileId: string, run: () => Promise<T>): Promise<T> => {
    setRefreshingTileIds((prev) => new Set(prev).add(tileId))
    try {
      return await run()
    } finally {
      setRefreshingTileIds((prev) => {
        const next = new Set(prev)
        next.delete(tileId)
        return next
      })
    }
  }, [])

  const refreshTile = useCallback(async (tileId: string) => {
    try {
      const res = await withRefreshing(tileId, () => api.refreshTile(tileId))
      setTileInCache(res.tile)
      return res.tile
    } catch (err) {
      notifyFailure(err, 'Failed to refresh tile.')
      return null
    }
  }, [setTileInCache, withRefreshing])

  const confirmTile = useCallback(async (tileId: string) => {
    try {
      const res = await api.confirmTile(tileId)
      setTileInCache(res.tile)
      return res.tile
    } catch (err) {
      notifyFailure(err, 'Failed to confirm tile.')
      return null
    }
  }, [setTileInCache])

  const updateTile = useCallback(async (tileId: string, req: UpdateTileRequest) => {
    try {
      const res = await api.updateTile(tileId, req)
      setTileInCache(res.tile)
      if (req.dashboard_id) void queryClient.invalidateQueries({ queryKey: ['tiles', req.dashboard_id] })
      return res.tile
    } catch (err) {
      notifyFailure(err, 'Failed to update tile.')
      throw err
    }
  }, [queryClient, setTileInCache])

  const deleteTile = useCallback(async (tileId: string) => {
    try {
      await api.deleteTile(tileId)
      queryClient.setQueryData<{ tiles: Tile[] }>(key, (old) =>
        old ? { tiles: old.tiles.filter((t) => t.tile_id !== tileId) } : old)
      void queryClient.invalidateQueries({ queryKey: ['tiles', 'all'] })
    } catch (err) {
      notifyFailure(err, 'Failed to unpin tile.')
      throw err
    }
  }, [queryClient, key])

  /** Store a layout: optimistic, rolled back with a message if the server refuses it. */
  const saveLayout = useCallback(async (items: LayoutItem[]) => {
    if (!dashboardId) return
    const before = queryClient.getQueryData<{ tiles: Tile[] }>(key)
    const slots = new Map(items.map((it) => [it.tile_id, it]))
    queryClient.setQueryData<{ tiles: Tile[] }>(key, (old) =>
      old
        ? {
            tiles: old.tiles.map((t) => {
              const s = slots.get(t.tile_id)
              return s ? { ...t, layout: { x: s.x, y: s.y, w: s.w, h: s.h } } : t
            }),
          }
        : old)
    try {
      await api.saveLayout(dashboardId, items)
    } catch (err) {
      queryClient.setQueryData(key, before)
      notifyFailure(err, 'Failed to save the layout.')
    }
  }, [queryClient, key, dashboardId])

  const refreshAll = useCallback(async () => {
    const tiles = query.data?.tiles ?? []
    if (tiles.length === 0) return
    setIsRefreshingAll(true)
    const executing = new Set<Promise<void>>()
    for (const tile of tiles) {
      const p: Promise<void> = (async () => {
        try {
          const res = await withRefreshing(tile.tile_id, () => api.refreshTile(tile.tile_id))
          setTileInCache(res.tile)
        } catch (err) {
          notifyFailure(err, 'Failed to refresh a tile.')
        }
      })()
      executing.add(p)
      p.finally(() => executing.delete(p))
      if (executing.size >= 3) await Promise.race(executing)   // at most 3 in flight
    }
    await Promise.all(executing)
    setIsRefreshingAll(false)
  }, [query.data?.tiles, setTileInCache, withRefreshing])

  return {
    ...query,
    tiles: query.data?.tiles ?? [],
    changed,
    refreshingTileIds,
    isRefreshingAll,
    refreshTile,
    confirmTile,
    updateTile,
    deleteTile,
    saveLayout,
    refreshAll,
  }
}

/** Every tile the principal has, for chat's "already pinned?" check. Not polled. */
export function useAllTiles() {
  return useQuery({ queryKey: ['tiles', 'all'], queryFn: () => api.tiles() })
}
