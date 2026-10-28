import { useRef, useState } from 'react'
import { FileText, FolderOpen, Library, Loader2, MoreHorizontal, Plus, Trash2, Upload, Pencil } from 'lucide-react'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { LibraryDocument, LibraryFolder } from '@/api/types'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Skeleton } from '@/components/ui/skeleton'
import { Tabs, TabsContent, TabsList, TabsTrigger } from '@/components/ui/tabs'
import { DatabasesTab } from '@/features/library/DatabasesTab'
import { ConfirmDeleteDialog, FolderNameDialog } from '@/features/library/FolderDialogs'
import { ACCEPT, contentTypeFor, folderDeleteWarning, formatBytes, pagesLabel, refusalFor } from '@/features/library/files'
import { useDocuments, useFolders } from '@/features/library/useLibrary'
import { formatRelativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'

function StatusBadge({ status }: { status: LibraryDocument['status'] }) {
  if (status === 'processing') {
    return (
      <Badge variant="outline" className="gap-1.5 font-normal">
        <Loader2 className="size-3 animate-spin" aria-hidden />
        Processing
      </Badge>
    )
  }
  if (status === 'failed') return <Badge variant="destructive">Failed</Badge>
  return (
    <Badge variant="outline" className="border-success/40 font-normal text-success">
      Ready
    </Badge>
  )
}

function DocumentRow({ doc, onDelete }: { doc: LibraryDocument; onDelete: (doc: LibraryDocument) => void }) {
  const { relative, absolute } = formatRelativeTime(doc.created_at)
  const pages = doc.content_type === 'application/pdf' ? pagesLabel(doc.pages) : null
  return (
    <li
      data-testid="library-document"
      data-status={doc.status}
      className="flex items-start gap-3 rounded-xl border bg-card p-4"
    >
      <div className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
        <FileText className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-1">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate font-medium text-foreground">{doc.title}</h3>
          <StatusBadge status={doc.status} />
        </div>
        <p className="flex flex-wrap gap-x-2 text-xs text-muted-foreground">
          <span className="truncate">{doc.filename}</span>
          {pages && <span>{pages}</span>}
          <span>{formatBytes(doc.bytes)}</span>
          <time dateTime={absolute} title={absolute}>
            {relative}
          </time>
        </p>
        {doc.status === 'failed' && doc.error && <p className="text-sm text-destructive">{doc.error}</p>}
      </div>
      <Button
        variant="ghost"
        size="icon"
        aria-label={`Delete document ${doc.title}`}
        onClick={() => onDelete(doc)}
        className="text-muted-foreground hover:text-destructive"
      >
        <Trash2 className="size-4" />
      </Button>
    </li>
  )
}

function UploadZone({ folderId, onUploaded, compact }: { folderId: string; onUploaded: () => void; compact?: boolean }) {
  const inputRef = useRef<HTMLInputElement>(null)
  const [dragging, setDragging] = useState(false)
  const [uploading, setUploading] = useState(false)

  const upload = async (files: File[]) => {
    if (files.length === 0) return
    setUploading(true)
    for (const file of files) {
      const refusal = refusalFor(file)
      if (refusal) {
        toast.error(refusal)
        continue
      }
      try {
        await api.uploadDocument(folderId, file, contentTypeFor(file.name)!)
        onUploaded()
      } catch (err) {
        toast.error(`${file.name}: ${err instanceof ApiError ? err.message : 'The upload failed.'}`)
      }
    }
    setUploading(false)
    onUploaded()
  }

  return (
    <div
      data-testid="upload-dropzone"
      onDragOver={(e) => {
        e.preventDefault()
        setDragging(true)
      }}
      onDragLeave={() => setDragging(false)}
      onDrop={(e) => {
        e.preventDefault()
        setDragging(false)
        void upload(Array.from(e.dataTransfer.files))
      }}
      className={cn(
        'flex flex-col items-center justify-center gap-3 rounded-xl border border-dashed p-6 text-center transition-colors',
        compact ? 'py-6' : 'h-56',
        dragging && 'border-primary bg-primary/5',
      )}
    >
      <Upload className="size-5 text-muted-foreground" aria-hidden />
      <p className="text-sm text-muted-foreground">Drop PDF, text or Markdown files here</p>
      <input
        ref={inputRef}
        type="file"
        multiple
        accept={ACCEPT}
        data-testid="upload-input"
        aria-label="Choose files to upload"
        className="sr-only"
        tabIndex={-1}
        onChange={(e) => {
          const files = Array.from(e.target.files ?? [])
          e.target.value = ''
          void upload(files)
        }}
      />
      <Button variant="outline" onClick={() => inputRef.current?.click()} disabled={uploading} className="gap-2">
        {uploading ? <Loader2 className="size-4 animate-spin" /> : <Upload className="size-4" />}
        Upload
      </Button>
    </div>
  )
}

type LibraryTab = 'documents' | 'databases'

/** The tab a link asks for: #/library/databases or #/library/documents (the default). */
function tabFromHash(hash: string): LibraryTab {
  return hash.replace(/^#\/?/, '').split('/')[1] === 'databases' ? 'databases' : 'documents'
}

/** The Data sources page (web/DESIGN.md §6b): the databases and document folders, added once and chosen per
 * workspace. A Databases tab and a Documents tab (folders on the left, the selected folder's documents on the
 * right). */
export function LibraryView() {
  const [tab, setTab] = useState<LibraryTab>(() => tabFromHash(window.location.hash))
  const { folders, isLoading, isError, error, refetch, createFolder, renameFolder, deleteFolder } = useFolders()
  const [selectedId, setSelectedId] = useState<string | null>(null)
  const [newOpen, setNewOpen] = useState(false)
  const [renaming, setRenaming] = useState<LibraryFolder | null>(null)
  const [deletingFolder, setDeletingFolder] = useState<LibraryFolder | null>(null)
  const [deletingDoc, setDeletingDoc] = useState<LibraryDocument | null>(null)

  const selected = folders.find((f) => f.folder_id === selectedId) ?? folders[0] ?? null
  const docs = useDocuments(selected?.folder_id ?? null)

  return (
    <div className="flex h-full flex-1 flex-col overflow-y-auto">
      <header className="flex min-h-14 shrink-0 items-center justify-between gap-4 border-b px-6 py-2">
        <div>
          <h1 className="text-xl font-semibold tracking-tight text-foreground">Data sources</h1>
          <p className="text-xs text-muted-foreground">
            Databases and documents, added once. Each workspace then chooses one database and any of these folders.
          </p>
        </div>
        {tab === 'documents' && (
          <Button onClick={() => setNewOpen(true)} className="gap-2">
            <Plus className="size-4" />
            New folder
          </Button>
        )}
      </header>

      <main className="flex-1 p-6">
        <Tabs value={tab} onValueChange={(v) => setTab(v as LibraryTab)} className="gap-6">
          <TabsList aria-label="Data source sections">
            <TabsTrigger value="databases">Databases</TabsTrigger>
            <TabsTrigger value="documents">Documents</TabsTrigger>
          </TabsList>
          <TabsContent value="documents" className="outline-none">
            {isLoading ? (
              <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
                <Skeleton className="h-40 rounded-xl" />
                <Skeleton className="h-64 rounded-xl" />
              </div>
            ) : isError ? (
              <div className="flex h-72 flex-col items-center justify-center gap-4 p-8 text-center">
                <p className="text-sm text-muted-foreground">
                  {error instanceof ApiError ? error.message : 'The library could not be loaded.'}
                </p>
                <Button onClick={() => void refetch()}>Retry</Button>
              </div>
            ) : folders.length === 0 ? (
              <div className="flex h-72 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center">
                <div className="mb-4 grid size-12 place-items-center rounded-xl bg-muted/60 text-muted-foreground">
                  <Library className="size-6" />
                </div>
                <h2 className="mb-1.5 text-base font-semibold text-foreground">No folders yet</h2>
                <p className="mb-4 max-w-md text-sm text-muted-foreground">
                  Folders hold the documents you upload, such as policies or manuals. Any workspace can search the
                  folders you choose for it.
                </p>
                <Button onClick={() => setNewOpen(true)} className="gap-2">
                  <Plus className="size-4" />
                  New folder
                </Button>
              </div>
            ) : (
              <div className="grid gap-6 md:grid-cols-[16rem_1fr]">
                <nav aria-label="Folders">
                  <ul className="space-y-1">
                    {folders.map((folder) => {
                      const active = folder.folder_id === selected?.folder_id
                      return (
                        <li key={folder.folder_id} className="group relative">
                          <button
                            type="button"
                            data-testid="folder-item"
                            aria-current={active ? 'true' : undefined}
                            onClick={() => setSelectedId(folder.folder_id)}
                            className={cn(
                              'flex w-full items-center gap-3 rounded-xl border border-transparent px-3 py-2 pr-11 text-left outline-none transition-colors',
                              'hover:bg-muted focus-visible:ring-2 focus-visible:ring-ring',
                              active && 'border-primary/30 bg-primary/10',
                            )}
                          >
                            <FolderOpen className={cn('size-4 shrink-0', active ? 'text-primary' : 'text-muted-foreground')} />
                            <span className="min-w-0">
                              <span className="block truncate text-sm font-medium text-foreground">{folder.name}</span>
                              <span className="block text-xs text-muted-foreground">
                                {folder.document_count} {folder.document_count === 1 ? 'document' : 'documents'}
                              </span>
                            </span>
                          </button>
                          {active && (
                            <DropdownMenu>
                              <DropdownMenuTrigger asChild>
                                <Button
                                  variant="ghost"
                                  size="icon"
                                  aria-label="Folder actions"
                                  className="absolute right-1 top-1/2 size-8 -translate-y-1/2"
                                >
                                  <MoreHorizontal className="size-4" />
                                </Button>
                              </DropdownMenuTrigger>
                              <DropdownMenuContent align="end">
                                <DropdownMenuItem onClick={() => setRenaming(folder)} className="gap-2">
                                  <Pencil className="size-4" />
                                  Rename folder
                                </DropdownMenuItem>
                                <DropdownMenuItem
                                  onClick={() => setDeletingFolder(folder)}
                                  className="gap-2 text-destructive focus:text-destructive"
                                >
                                  <Trash2 className="size-4" />
                                  Delete folder
                                </DropdownMenuItem>
                              </DropdownMenuContent>
                            </DropdownMenu>
                          )}
                        </li>
                      )
                    })}
                  </ul>
                </nav>

                <section aria-label={selected ? `Documents in ${selected.name}` : 'Documents'} className="min-w-0 space-y-4">
                  {selected && (
                    <>
                      <h2 className="text-base font-semibold text-foreground">{selected.name}</h2>
                      {docs.isLoading ? (
                        <div className="space-y-3">
                          <Skeleton className="h-20 rounded-xl" />
                          <Skeleton className="h-20 rounded-xl" />
                        </div>
                      ) : docs.isError ? (
                        <div className="flex flex-col items-start gap-3">
                          <p className="text-sm text-muted-foreground">
                            {docs.error instanceof ApiError ? docs.error.message : 'The documents could not be loaded.'}
                          </p>
                          <Button variant="outline" onClick={() => void docs.refetch()}>
                            Retry
                          </Button>
                        </div>
                      ) : docs.documents.length === 0 ? (
                        <div className="space-y-3">
                          <div>
                            <h3 className="font-medium text-foreground">No documents in this folder</h3>
                            <p className="text-sm text-muted-foreground">Upload a file and it becomes searchable once it is ready.</p>
                          </div>
                          <UploadZone folderId={selected.folder_id} onUploaded={docs.refresh} />
                        </div>
                      ) : (
                        <>
                          <ul className="space-y-3">
                            {docs.documents.map((doc) => (
                              <DocumentRow key={doc.document_id} doc={doc} onDelete={setDeletingDoc} />
                            ))}
                          </ul>
                          <UploadZone folderId={selected.folder_id} onUploaded={docs.refresh} compact />
                        </>
                      )}
                    </>
                  )}
                </section>
              </div>
            )}
          </TabsContent>
          <TabsContent value="databases" className="outline-none">
            <DatabasesTab />
          </TabsContent>
        </Tabs>
      </main>

      <FolderNameDialog
        open={newOpen}
        onOpenChange={setNewOpen}
        title="New folder"
        submitLabel="Create"
        onSubmit={async (name) => {
          const res = await createFolder(name)
          setSelectedId(res.folder.folder_id)
        }}
      />
      {renaming && (
        <FolderNameDialog
          key={renaming.folder_id}
          open
          onOpenChange={(open) => !open && setRenaming(null)}
          title="Rename folder"
          submitLabel="Save"
          initialName={renaming.name}
          onSubmit={(name) => renameFolder({ folderId: renaming.folder_id, name })}
        />
      )}
      {deletingFolder && (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeletingFolder(null)}
          title="Delete folder"
          onConfirm={async () => {
            await deleteFolder(deletingFolder.folder_id)
            setSelectedId(null)
            toast.success('Folder deleted')
          }}
        >
          <p>
            Delete <span className="font-semibold">{deletingFolder.name}</span>?
          </p>
          {folderDeleteWarning(deletingFolder).map((line) => (
            <p key={line}>{line}</p>
          ))}
        </ConfirmDeleteDialog>
      )}
      {deletingDoc && (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeletingDoc(null)}
          title="Delete document"
          onConfirm={async () => {
            await docs.deleteDocument(deletingDoc.document_id)
            toast.success('Document deleted')
          }}
        >
          <p>
            Delete <span className="font-semibold">{deletingDoc.title}</span>? Workspaces will no longer find it.
          </p>
        </ConfirmDeleteDialog>
      )}
    </div>
  )
}
