import { useState } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useFolders, useWorkspaceFolders } from '@/features/library/useLibrary'

/** A workspace's Documents tab (web/DESIGN.md §6b): which library folders its questions may search. */
export function DocumentsTab({ workspaceId }: { workspaceId: string }) {
  const folders = useFolders()
  const chosen = useWorkspaceFolders(workspaceId)
  const [edited, setEdited] = useState<string[] | null>(null)
  const [saving, setSaving] = useState(false)
  const [saveError, setSaveError] = useState<string | null>(null)

  const saved = chosen.folderIds
  const current = edited ?? saved ?? []
  const changed = edited !== null && saved !== null && (edited.length !== saved.length || edited.some((id) => !saved.includes(id)))

  const openLibrary = (
    <a href="#/library" className="text-sm font-medium text-primary underline-offset-4 hover:underline">
      Open library
    </a>
  )

  if (folders.isLoading || chosen.isLoading) {
    return (
      <div className="max-w-xl space-y-3">
        <Skeleton className="h-5 w-80" />
        <Skeleton className="h-10 w-full" />
        <Skeleton className="h-10 w-full" />
      </div>
    )
  }
  if (folders.isError || chosen.isError) {
    const err = folders.error ?? chosen.error
    return (
      <div className="flex max-w-xl flex-col items-start gap-3">
        <p className="text-sm text-muted-foreground">
          {err instanceof ApiError ? err.message : 'The documents could not be loaded.'}
        </p>
        <Button
          variant="outline"
          onClick={() => {
            void folders.refetch()
            void chosen.refetch()
          }}
        >
          Retry
        </Button>
      </div>
    )
  }

  const toggle = (id: string, on: boolean) => {
    setSaveError(null)
    setEdited(on ? [...new Set([...current, id])] : current.filter((x) => x !== id))
  }

  const save = async () => {
    if (!changed || saving) return
    setSaving(true)
    setSaveError(null)
    try {
      await chosen.save(edited!)
      setEdited(null)
      toast.success('Documents saved')
    } catch (err) {
      setSaveError(err instanceof ApiError ? err.message : 'The folders could not be saved.')
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="max-w-xl space-y-4">
      <p className="text-sm text-muted-foreground">
        Questions in this workspace can search the documents in the folders you choose.
      </p>
      {folders.folders.length === 0 ? (
        <div className="space-y-2">
          <p className="text-sm font-medium text-foreground">Your library is empty.</p>
          {openLibrary}
        </div>
      ) : (
        <>
          <ul className="space-y-2">
            {folders.folders.map((folder) => (
              <li key={folder.folder_id}>
                <label
                  data-testid="workspace-folder"
                  className="flex cursor-pointer items-center gap-3 rounded-xl border bg-card px-4 py-3 text-sm"
                >
                  <input
                    type="checkbox"
                    checked={current.includes(folder.folder_id)}
                    onChange={(e) => toggle(folder.folder_id, e.target.checked)}
                    className="size-4 accent-primary"
                  />
                  <span className="font-medium text-foreground">{folder.name}</span>
                  <span className="text-xs text-muted-foreground">{folder.ready_count} ready</span>
                </label>
              </li>
            ))}
          </ul>
          {saveError && (
            <p role="alert" className="text-sm text-destructive">
              {saveError}
            </p>
          )}
          <div className="flex items-center gap-4">
            <Button onClick={() => void save()} disabled={!changed || saving}>
              {saving ? 'Saving…' : 'Save'}
            </Button>
            {openLibrary}
          </div>
        </>
      )}
    </div>
  )
}
