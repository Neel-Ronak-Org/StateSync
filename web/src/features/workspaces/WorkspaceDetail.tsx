import { useEffect, useState } from 'react'
import { ArrowLeft, MessageSquare, MoreHorizontal, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { BusinessTab } from '@/features/workspaces/BusinessTab'
import { KnowledgeTab } from '@/features/workspaces/KnowledgeTab'
import { TestTab } from '@/features/workspaces/TestTab'
import {
  useApproveWorkspace,
  useCatalog,
  useDefinitions,
  useDraftStatus,
  useSetupStatus,
  useWorkspace,
} from '@/features/workspaces/useWorkspaces'

interface WorkspaceDetailProps {
  workspaceId: string
  onAskInWorkspace: (workspaceId: string, targetSchema: string) => void
  onDeleteWorkspace: (workspaceId: string) => Promise<unknown>
}

/** Detail view of a single workspace showing Business, Knowledge, and Test steps. */
export function WorkspaceDetail({
  workspaceId,
  onAskInWorkspace,
  onDeleteWorkspace,
}: WorkspaceDetailProps) {
  const { workspace, isLoading, isError, error, updateWorkspace } = useWorkspace(workspaceId)
  const { catalog, updateDescriptions } = useCatalog(workspaceId)
  const { definitions, addDefinitions, deleteDefinition } = useDefinitions(workspaceId)
  const { job: draftJob, startDraft } = useDraftStatus(workspaceId)
  const {
    job: setupJob,
    run: setupRun,
    isRunning: isSetupRunning,
    startSetup,
    setVerdict,
  } = useSetupStatus(workspaceId)
  const { approve } = useApproveWorkspace(workspaceId)

  const [activeTab, setActiveTab] = useState<string>('knowledge')
  const [hasSetInitialTab, setHasSetInitialTab] = useState(false)
  const [deleteConfirmOpen, setDeleteConfirmOpen] = useState(false)
  const [isDeleting, setIsDeleting] = useState(false)

  // Set initial tab based on setup_state
  useEffect(() => {
    if (workspace && !hasSetInitialTab) {
      const initial =
        workspace.setup_state === 'approved' || workspace.setup_state === 'tested'
          ? 'test'
          : 'knowledge'
      setActiveTab(initial)
      setHasSetInitialTab(true)
    }
  }, [workspace, hasSetInitialTab])

  if (isLoading) {
    return (
      <div className="p-6 space-y-4">
        <Skeleton className="h-8 w-48" />
        <Skeleton className="h-4 w-96" />
        <Skeleton className="h-64 w-full" />
      </div>
    )
  }

  if (isError || !workspace) {
    // A 404 means it is gone (or someone else's); anything else may pass.
    const missing = !isError || (error instanceof ApiError && error.status === 404)
    return (
      <div className="flex h-72 flex-col items-center justify-center p-8 text-center space-y-4">
        <p className="text-sm text-muted-foreground">
          {missing
            ? 'This workspace does not exist'
            : error instanceof ApiError
              ? error.message
              : 'The workspace could not be loaded.'}
        </p>
        <Button onClick={() => (window.location.hash = '#/workspaces')}>All workspaces</Button>
      </div>
    )
  }

  const handleDelete = async () => {
    setIsDeleting(true)
    try {
      await onDeleteWorkspace(workspace.workspace_id)
      setDeleteConfirmOpen(false)
      toast.success('Workspace deleted')
      window.location.hash = '#/workspaces'
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to delete workspace.')
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <div className="flex h-full flex-1 flex-col overflow-y-auto">
      {/* Top Header */}
      <header className="border-b bg-background/95 px-6 py-4 backdrop-blur">
        <div className="flex flex-wrap items-center justify-between gap-4">
          <div className="flex items-center gap-3">
            <a
              href="#/workspaces"
              className="grid size-8 place-items-center rounded-lg text-muted-foreground hover:bg-muted hover:text-foreground transition-colors"
              aria-label="Back to workspaces"
            >
              <ArrowLeft className="size-4" />
            </a>
            <h1 className="text-xl font-semibold tracking-tight text-foreground">
              {workspace.name}
            </h1>
          </div>

          <div className="flex items-center gap-2">
            <Button
              onClick={() => onAskInWorkspace(workspace.workspace_id, workspace.target_schema)}
              className="gap-2"
            >
              <MessageSquare className="size-4" />
              Ask in this workspace
            </Button>

            <DropdownMenu>
              <DropdownMenuTrigger asChild>
                <Button variant="outline" size="icon" aria-label="Workspace actions">
                  <MoreHorizontal className="size-4" />
                </Button>
              </DropdownMenuTrigger>
              <DropdownMenuContent align="end">
                <DropdownMenuItem
                  onClick={() => setDeleteConfirmOpen(true)}
                  className="text-destructive focus:text-destructive gap-2"
                >
                  <Trash2 className="size-4" />
                  Delete workspace
                </DropdownMenuItem>
              </DropdownMenuContent>
            </DropdownMenu>
          </div>
        </div>
      </header>

      {/* Main setup tabs */}
      <main className="flex-1 p-6">
        <Tabs value={activeTab} onValueChange={setActiveTab} className="space-y-6">
          <TabsList aria-label="Setup steps" className="grid w-full max-w-md grid-cols-3">
            <TabsTrigger value="business">Business</TabsTrigger>
            <TabsTrigger value="knowledge">Knowledge</TabsTrigger>
            <TabsTrigger value="test">Test</TabsTrigger>
          </TabsList>

          <TabsContent value="business" className="outline-none">
            <BusinessTab workspace={workspace} onUpdateWorkspace={updateWorkspace} />
          </TabsContent>

          <TabsContent value="knowledge" className="outline-none">
            <KnowledgeTab
              catalog={catalog}
              definitions={definitions}
              draftJob={draftJob}
              onStartDraft={startDraft}
              onSaveDescriptions={updateDescriptions}
              onAddDefinition={addDefinitions}
              onDeleteDefinition={deleteDefinition}
              onApprove={approve}
              onSwitchToTest={() => setActiveTab('test')}
            />
          </TabsContent>

          <TabsContent value="test" className="outline-none">
            <TestTab
              setupState={workspace.setup_state}
              targetSchema={workspace.target_schema}
              job={setupJob}
              run={setupRun}
              isRunning={isSetupRunning}
              onStartSetup={startSetup}
              onSetVerdict={setVerdict}
            />
          </TabsContent>
        </Tabs>
      </main>

      {/* Delete Confirmation Dialog */}
      <Dialog open={deleteConfirmOpen} onOpenChange={setDeleteConfirmOpen}>
        <DialogContent className="sm:max-w-md">
          <DialogHeader>
            <DialogTitle>Delete workspace</DialogTitle>
            <DialogDescription>
              Are you sure you want to delete <span className="font-semibold">{workspace.name}</span>?
              This will remove all descriptions, business definitions, and validation tests.
            </DialogDescription>
          </DialogHeader>
          <DialogFooter className="pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => setDeleteConfirmOpen(false)}
              disabled={isDeleting}
            >
              Cancel
            </Button>
            <Button
              type="button"
              variant="destructive"
              onClick={handleDelete}
              disabled={isDeleting}
            >
              {isDeleting ? 'Deleting…' : 'Delete'}
            </Button>
          </DialogFooter>
        </DialogContent>
      </Dialog>
    </div>
  )
}
