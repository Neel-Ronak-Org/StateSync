import type { ReactNode } from 'react'
import { BookOpenCheck, Check, Database, FileText, MessageSquareQuote, PenLine } from 'lucide-react'

import type { CatalogEntry, Workspace } from '@/api/types'
import { Button } from '@/components/ui/button'
import { useFolders, useWorkspaceFolders } from '@/features/library/useLibrary'
import { useConnections } from '@/features/library/useConnections'

interface OverviewTabProps {
  workspace: Workspace
  catalog: CatalogEntry[]
  onOpenTab: (tab: string) => void
}

interface StepProps {
  icon: ReactNode
  title: string
  done: boolean
  children: ReactNode
  action: ReactNode
}

function Step({ icon, title, done, children, action }: StepProps) {
  return (
    <li
      data-testid="setup-step"
      data-done={String(done)}
      className="flex flex-wrap items-start gap-4 rounded-xl border bg-card px-4 py-3.5"
    >
      <div className="grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">{icon}</div>
      <div className="min-w-0 flex-1 space-y-0.5">
        <div className="flex items-center gap-2">
          <h3 className="text-sm font-medium">{title}</h3>
          {done ? (
            <span className="inline-flex items-center gap-1 text-xs text-emerald-500">
              <Check className="size-3.5" /> Done
            </span>
          ) : (
            <span className="text-xs text-amber-500">To do</span>
          )}
        </div>
        <div className="text-sm text-muted-foreground">{children}</div>
      </div>
      <div className="flex shrink-0 items-center gap-2">{action}</div>
    </li>
  )
}

/** The workspace at a glance: what it answers from, what the owner has told it, and where to change each. */
export function OverviewTab({ workspace, catalog, onOpenTab }: OverviewTabProps) {
  const { connections } = useConnections()
  const folders = useFolders()
  const chosen = useWorkspaceFolders(workspace.workspace_id)

  const connection = connections.find((c) => c.connection_id === workspace.target_schema)
  const chosenFolders = (folders.folders ?? []).filter((f) => (chosen.folderIds ?? []).includes(f.folder_id))
  const readyDocs = chosenFolders.reduce((n, f) => n + f.ready_count, 0)
  const described = catalog.filter((e) => e.description)
  const approvedNotes = described.filter((e) => e.approved).length
  const tables = new Set(catalog.map((e) => e.table_name)).size

  const edit = (tab: string, label: string) => (
    <Button variant="outline" size="sm" onClick={() => onOpenTab(tab)}>
      {label}
    </Button>
  )
  const link = (href: string, label: string) => (
    <Button variant="ghost" size="sm" asChild>
      <a href={href}>{label}</a>
    </Button>
  )

  return (
    <div className="max-w-3xl space-y-6">
      <p className="text-sm text-muted-foreground">
        A workspace is one business: <span className="text-foreground">one database</span>, the{' '}
        <span className="text-foreground">document folders</span> it may search, and{' '}
        <span className="text-foreground">what you have told StateSync</span> about it. Every question asked in
        this workspace uses exactly these. Databases and documents are added once under{' '}
        <a href="#/library" className="text-primary underline-offset-4 hover:underline">
          Data sources
        </a>
        ; here you choose and describe them.
      </p>

      <ol className="space-y-2.5" aria-label="Setup">
        <Step
          icon={<Database className="size-4" />}
          title="1. Database"
          done={Boolean(connection)}
          action={
            <>
              {edit('schema', 'View tables')}
              {link('#/library/databases', 'Manage')}
            </>
          }
        >
          {connection ? (
            <>
              <span className="text-foreground">{connection.name}</span> · schema{' '}
              <span className="font-mono text-xs">{connection.schema_name ?? 'public'}</span> · {tables} tables and
              views · read-only login
            </>
          ) : (
            'The database this workspace was created on is no longer connected.'
          )}
        </Step>

        <Step
          icon={<FileText className="size-4" />}
          title="2. Documents"
          done={chosenFolders.length > 0}
          action={
            <>
              {edit('documents', 'Choose folders')}
              {link('#/library/documents', 'Upload')}
            </>
          }
        >
          {chosenFolders.length > 0 ? (
            <>
              {chosenFolders.map((f) => f.name).join(', ')} · {readyDocs} document{readyDocs === 1 ? '' : 's'}{' '}
              ready to search
            </>
          ) : (
            'No folders chosen: questions are answered from the database only.'
          )}
        </Step>

        <Step
          icon={<PenLine className="size-4" />}
          title="3. About the business"
          done={Boolean(workspace.business_context.trim())}
          action={edit('business', 'Edit')}
        >
          {workspace.business_context.trim()
            ? `“${workspace.business_context.trim().slice(0, 160)}${workspace.business_context.trim().length > 160 ? '…' : ''}”`
            : 'A few lines in your words. Optional, but it helps the router and the SQL writer.'}
        </Step>

        <Step
          icon={<BookOpenCheck className="size-4" />}
          title="4. Table and column notes"
          done={described.length > 0 && approvedNotes === described.length}
          action={edit('knowledge', approvedNotes === described.length && described.length > 0 ? 'Edit' : 'Review')}
        >
          {described.length > 0
            ? `${approvedNotes} of ${described.length} notes approved, ${workspace.counts.definitions} business definition${workspace.counts.definitions === 1 ? '' : 's'}. Only approved text is used.`
            : 'Not drafted yet: press Draft with AI, edit, then Approve.'}
        </Step>

        <Step
          icon={<MessageSquareQuote className="size-4" />}
          title="5. Approved answers"
          done={workspace.counts.examples > 0}
          action={edit('answers', 'View')}
        >
          {workspace.counts.examples > 0
            ? `${workspace.counts.examples} question${workspace.counts.examples === 1 ? '' : 's'} with an approved query; asked again, they are answered from it and labelled Verified.`
            : 'Optional. A thumbs-up on a correct database answer in Ask adds it here.'}
        </Step>
      </ol>
    </div>
  )
}
