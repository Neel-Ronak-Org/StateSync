import { FileSearch } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import { useFolders, useWorkspaceFolders } from '@/features/library/useLibrary'
import { useMiningStatus } from '@/features/workspaces/useWorkspaces'

function plural(n: number, one: string, many: string): string {
  return `${n} ${n === 1 ? one : many}`
}

/**
 * "Find in documents" (web/DESIGN.md §6a): proposes definitions found in the
 * workspace's uploaded documents. Each comes with the sentence it was taken
 * from; proposals whose sentence is not really in the document are left out
 * by the server and only counted here.
 */
export function DocumentDefinitions({ workspaceId }: { workspaceId: string }) {
  const { folders } = useFolders()
  const { folderIds } = useWorkspaceFolders(workspaceId)
  const { job, startMining, isStarting } = useMiningStatus(workspaceId)

  const chosen = new Set(folderIds ?? [])
  const readyDocuments = folders.filter((f) => chosen.has(f.folder_id)).reduce((n, f) => n + f.ready_count, 0)
  const running = job?.state === 'running'

  const start = async () => {
    try {
      await startMining()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Could not start reading the documents.')
    }
  }

  return (
    <div className="space-y-2 rounded-xl border bg-muted/20 p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div className="min-w-0 space-y-0.5">
          <h4 className="text-xs font-medium text-foreground">From your documents</h4>
          <p className="text-xs text-muted-foreground">
            {readyDocuments > 0
              ? `Suggest definitions written in the ${plural(readyDocuments, 'document', 'documents')} this workspace searches, each with the sentence it came from.`
              : 'Choose library folders with ready documents in the Documents tab to find definitions in them.'}
          </p>
        </div>
        {running ? (
          <div
            data-testid="mine-progress"
            className="flex items-center gap-2 rounded-lg bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary animate-pulse"
          >
            <FileSearch className="size-3.5" aria-hidden />
            <span>
              Reading documents… {job?.done ?? 0}/{job?.total || 1}
            </span>
          </div>
        ) : (
          <Button
            type="button"
            variant="outline"
            size="sm"
            className="gap-2"
            onClick={start}
            disabled={readyDocuments === 0 || isStarting}
          >
            <FileSearch className="size-4" aria-hidden />
            Find in documents
          </Button>
        )}
      </div>

      {job && job.state !== 'running' && (
        <div data-testid="mine-summary" className="space-y-0.5 text-xs">
          {job.state === 'done' && (
            <p className="text-foreground">
              {job.found
                ? `Found ${plural(job.found, 'definition', 'definitions')} to review below.`
                : 'No new definitions were found.'}
            </p>
          )}
          {job.dropped ? (
            <p className="text-muted-foreground">
              {plural(job.dropped, 'suggestion was', 'suggestions were')} left out because the quoted sentence is
              not in the document.
            </p>
          ) : null}
          {job.error && <p className={job.state === 'failed' ? 'text-destructive' : 'text-muted-foreground'}>{job.error}</p>}
        </div>
      )}
    </div>
  )
}
