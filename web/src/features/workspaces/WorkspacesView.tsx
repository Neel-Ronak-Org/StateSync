import { useState } from 'react'
import { Boxes, Database, Plus } from 'lucide-react'

import type { SetupState, Workspace } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Skeleton } from '@/components/ui/skeleton'
import { useDatasetLabel } from '@/features/library/useConnections'
import { NewWorkspaceDialog } from '@/features/workspaces/NewWorkspaceDialog'

/** Where a workspace is in its setup, in words a person reads. */
const SETUP_STATE_LABEL: Record<SetupState, string> = {
  created: 'New',
  scanned: 'Scanned',
  drafted: 'Drafts to review',
  approved: 'Approved, not tested',
  tested: 'Tested',
}

interface WorkspacesViewProps {
  workspaces: Workspace[]
  isLoading?: boolean
  availableSchemas: string[]
  onCreateWorkspace: (data: {
    name: string
    target_schema: string
    business_context?: string
  }) => Promise<{ workspace: Workspace }>
  onCreated: (workspace: Workspace) => void
}

/** The Workspaces list page showing all configured workspaces. */
export function WorkspacesView({
  workspaces,
  isLoading = false,
  availableSchemas,
  onCreateWorkspace,
  onCreated,
}: WorkspacesViewProps) {
  const [newOpen, setNewOpen] = useState(false)
  const label = useDatasetLabel(workspaces.map((w) => w.target_schema))

  return (
    <div className="flex h-full flex-1 flex-col overflow-y-auto">
      {/* Header */}
      <header className="flex h-14 shrink-0 items-center justify-between border-b px-6">
        <h1 className="text-xl font-semibold tracking-tight text-foreground">Workspaces</h1>
        <Button onClick={() => setNewOpen(true)} className="gap-2">
          <Plus className="size-4" />
          New workspace
        </Button>
      </header>

      {/* Main Content */}
      <main className="flex-1 p-6">
        {isLoading ? (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {[0, 1, 2].map((i) => (
              <Skeleton key={i} className="h-32 rounded-xl" />
            ))}
          </div>
        ) : workspaces.length === 0 ? (
          <div className="flex h-72 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center">
            <div className="grid size-12 place-items-center rounded-xl bg-muted/60 text-muted-foreground mb-4">
              <Boxes className="size-6" />
            </div>
            <h2 className="text-base font-semibold text-foreground mb-1.5">No workspaces yet</h2>
            <p className="text-sm text-muted-foreground max-w-md">
              A workspace is your data plus what StateSync learns about it: what each table and column
              means, your business terms, and a 10-question test that shows how well it answers.
            </p>
          </div>
        ) : (
          <div className="grid grid-cols-1 gap-4 sm:grid-cols-2 lg:grid-cols-3">
            {workspaces.map((ws) => (
              <a
                key={ws.workspace_id}
                href={`#/workspaces/${ws.workspace_id}`}
                data-testid="workspace-card"
                className="group flex flex-col justify-between rounded-xl border bg-card p-5 transition-all hover:border-primary/50 hover:shadow-sm"
              >
                <div className="space-y-2">
                  <div className="flex items-start justify-between gap-2">
                    <h3 className="font-medium text-foreground group-hover:text-primary transition-colors">
                      {ws.name}
                    </h3>
                    <Badge variant="outline" className="shrink-0 text-xs font-normal">
                      {SETUP_STATE_LABEL[ws.setup_state]}
                    </Badge>
                  </div>
                  <div className="flex items-center gap-2 text-xs text-muted-foreground">
                    <Database className="size-3.5" />
                    <span>{label(ws.target_schema)}</span>
                  </div>
                </div>

                {ws.setup_score !== null && (
                  <div className="mt-4 pt-3 border-t flex items-center justify-between text-xs">
                    <span className="text-muted-foreground">Setup score</span>
                    <span className="font-semibold text-foreground">
                      {ws.setup_score}/{ws.setup_total ?? 10}
                    </span>
                  </div>
                )}
              </a>
            ))}
          </div>
        )}
      </main>

      <NewWorkspaceDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        availableSchemas={availableSchemas}
        onCreateWorkspace={onCreateWorkspace}
        onCreated={onCreated}
      />
    </div>
  )
}
