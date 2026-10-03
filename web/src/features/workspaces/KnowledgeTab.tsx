import { useMemo, useState } from 'react'
import { Sparkles, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import type { CatalogEntry, Definition, SetupJob } from '@/api/types'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { DocumentDefinitions } from '@/features/workspaces/DocumentDefinitions'

const SOURCE_LABEL: Record<Definition['source'], string> = {
  ai: 'AI draft',
  document: 'From document',
  owner: 'Yours',
}

interface KnowledgeTabProps {
  workspaceId: string
  catalog: CatalogEntry[]
  definitions: Definition[]
  draftJob: SetupJob | null
  onStartDraft: () => Promise<unknown>
  onSaveDescriptions: (
    items: { table_name: string; column_name: string; description: string | null }[],
  ) => Promise<unknown>
  onAddDefinition: (items: { term: string; definition: string }[]) => Promise<unknown>
  onDeleteDefinition: (definitionId: string) => Promise<unknown>
  onApprove: () => Promise<unknown>
  onSwitchToTest: () => void
}

/** The Knowledge tab showing catalog descriptions, business terms, and approval. */
export function KnowledgeTab({
  workspaceId,
  catalog,
  definitions,
  draftJob,
  onStartDraft,
  onSaveDescriptions,
  onAddDefinition,
  onDeleteDefinition,
  onApprove,
  onSwitchToTest,
}: KnowledgeTabProps) {
  const isDraftRunning = draftJob?.state === 'running'
  const draftFailed = draftJob?.state === 'failed' && Boolean(draftJob?.error)

  const [edits, setEdits] = useState<Record<string, string>>({})
  const [isSaving, setIsSaving] = useState(false)
  const [isApproving, setIsApproving] = useState(false)

  // Add definition form state
  const [newTerm, setNewTerm] = useState('')
  const [newDefinition, setNewDefinition] = useState('')
  const [isAddingDef, setIsAddingDef] = useState(false)

  // Group catalog by table_name
  const groupedCatalog = useMemo(() => {
    const map = new Map<string, CatalogEntry[]>()
    for (const entry of catalog) {
      const list = map.get(entry.table_name) ?? []
      list.push(entry)
      map.set(entry.table_name, list)
    }
    // Sort so table row (column_name === '') is first within each table
    for (const [table, list] of map.entries()) {
      list.sort((a, b) => {
        if (a.column_name === '') return -1
        if (b.column_name === '') return 1
        return a.column_name.localeCompare(b.column_name)
      })
      map.set(table, list)
    }
    return map
  }, [catalog])

  const changedEntries = useMemo(() => {
    return catalog.filter((entry) => {
      const key = `${entry.table_name}:${entry.column_name}`
      const val = edits[key]
      return val !== undefined && val !== (entry.description ?? '')
    })
  }, [catalog, edits])

  const described = catalog.filter((e) => e.description)
  const approvedCount = described.filter((e) => e.approved).length
  const hasUnsaved = changedEntries.length > 0
  const approveBlocked = isDraftRunning || hasUnsaved || described.length === 0

  const handleEditChange = (table: string, column: string, value: string) => {
    const key = `${table}:${column}`
    setEdits((prev) => ({ ...prev, [key]: value }))
  }

  const handleSaveChanges = async () => {
    if (changedEntries.length === 0 || isSaving) return
    setIsSaving(true)
    try {
      const items = changedEntries.map((e) => {
        const key = `${e.table_name}:${e.column_name}`
        const desc = edits[key]?.trim() || null
        return {
          table_name: e.table_name,
          column_name: e.column_name,
          description: desc,
        }
      })
      await onSaveDescriptions(items)
      setEdits({})
      toast.success('Descriptions saved')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to save descriptions.')
    } finally {
      setIsSaving(false)
    }
  }

  const handleDraftWithAi = async () => {
    try {
      await onStartDraft()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to start AI drafting.')
    }
  }

  const handleAddDef = async () => {
    if (!newTerm.trim() || !newDefinition.trim() || isAddingDef) return
    setIsAddingDef(true)
    try {
      await onAddDefinition([
        {
          term: newTerm.trim(),
          definition: newDefinition.trim(),
        },
      ])
      setNewTerm('')
      setNewDefinition('')
      toast.success('Definition added')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to add definition.')
    } finally {
      setIsAddingDef(false)
    }
  }

  const handleDeleteDef = async (defId: string, term: string) => {
    try {
      await onDeleteDefinition(defId)
      toast.success(`Deleted definition ${term}`)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to delete definition.')
    }
  }

  const handleApprove = async () => {
    setIsApproving(true)
    try {
      await onApprove()
      toast.success('Approved')
      onSwitchToTest()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to approve knowledge.')
    } finally {
      setIsApproving(false)
    }
  }

  return (
    <div className="space-y-8 py-4">
      {/* Top action bar / draft status */}
      <div className="flex flex-wrap items-center justify-between gap-4">
        <div className="flex items-center gap-3">
          {!isDraftRunning && catalog.length > 0 && (
            <Button onClick={handleDraftWithAi} className="gap-2">
              <Sparkles className="size-4" />
              Draft with AI
            </Button>
          )}

          {isDraftRunning && (
            <div
              data-testid="draft-progress"
              className="flex items-center gap-2 rounded-lg bg-primary/10 px-3 py-1.5 text-xs font-medium text-primary animate-pulse"
            >
              <Sparkles className="size-3.5 animate-spin" />
              <span>
                Drafting descriptions… {draftJob?.done ?? 0}/{draftJob?.total ?? 1}
              </span>
            </div>
          )}

          <Button
            variant="outline"
            onClick={handleSaveChanges}
            disabled={changedEntries.length === 0 || isSaving}
          >
            {isSaving ? 'Saving…' : 'Save changes'}
          </Button>
        </div>

        <div className="flex flex-col items-end gap-1">
          <Button onClick={handleApprove} disabled={isApproving || approveBlocked}>
            {isApproving ? 'Approving…' : 'Approve'}
          </Button>
          <span className="text-[11px] text-muted-foreground" data-testid="approval-summary">
            {hasUnsaved
              ? 'Save your changes before approving'
              : `${approvedCount} of ${described.length} descriptions approved`}
          </span>
        </div>
      </div>

      <p className="text-xs text-muted-foreground">
        Only approved descriptions and definitions are used when answering. Any edit needs approving again.
      </p>

      {draftFailed && (
        <Alert variant="destructive">
          <AlertDescription>{draftJob?.error}</AlertDescription>
        </Alert>
      )}

      {catalog.length === 0 && !isDraftRunning && (
        <div className="rounded-xl border border-dashed p-8 text-center">
          <h3 className="text-sm font-medium text-foreground mb-1">No descriptions yet</h3>
          <p className="text-xs text-muted-foreground mb-4 max-w-sm mx-auto">
            Scan your database and draft column descriptions with AI to establish business knowledge.
          </p>
          <Button onClick={handleDraftWithAi} className="gap-2">
            <Sparkles className="size-4" />
            Draft with AI
          </Button>
        </div>
      )}

      {/* Catalog tables */}
      {catalog.length > 0 && (
        <div className="space-y-6">
          <h3 className="text-sm font-semibold tracking-tight text-foreground uppercase text-xs">
            Schema Catalog
          </h3>

          <div className="space-y-6">
            {Array.from(groupedCatalog.entries()).map(([table, entries]) => (
              <div key={table} className="rounded-xl border bg-card overflow-hidden">
                <div className="border-b bg-muted/40 px-4 py-2.5 flex items-center justify-between">
                  <span className="font-mono text-xs font-semibold text-foreground">{table}</span>
                  <span className="text-[11px] text-muted-foreground">
                    {entries.filter((e) => e.column_name !== '').length} columns
                  </span>
                </div>

                <div className="divide-y divide-border">
                  {entries.map((entry) => {
                    const isTable = entry.column_name === ''
                    const rowKey = `${entry.table_name}:${entry.column_name}`
                    const label = isTable
                      ? `Description of ${entry.table_name}`
                      : `Description of ${entry.table_name}.${entry.column_name}`
                    const currentDesc =
                      edits[rowKey] !== undefined ? edits[rowKey] : (entry.description ?? '')

                    return (
                      <div
                        key={rowKey}
                        data-testid="catalog-row"
                        data-table={entry.table_name}
                        data-column={entry.column_name}
                        data-approved={String(entry.approved)}
                        className="p-4 space-y-2.5 transition-colors hover:bg-muted/20"
                      >
                        <div className="flex flex-wrap items-center justify-between gap-2">
                          <div className="flex items-center gap-2">
                            <span className="font-mono text-xs font-medium text-foreground">
                              {isTable ? entry.table_name : entry.column_name}
                            </span>
                            <Badge variant="secondary" className="font-mono text-[10px] font-normal">
                              {entry.data_type}
                            </Badge>
                          </div>

                          {entry.description_source && (
                            <Badge
                              variant="outline"
                              data-testid="source-badge"
                              className="text-[11px] font-normal"
                            >
                              {entry.description_source === 'ai' ? 'AI draft' : 'Yours'}
                            </Badge>
                          )}
                        </div>

                        {entry.sample_values && entry.sample_values.length > 0 && (
                          <div className="flex flex-wrap items-center gap-1.5 pt-0.5">
                            <span className="text-[11px] text-muted-foreground">Samples:</span>
                            {entry.sample_values.map((sample, sIdx) => (
                              <span
                                key={sIdx}
                                className="rounded bg-muted px-1.5 py-0.5 font-mono text-[11px] text-muted-foreground"
                              >
                                {sample}
                              </span>
                            ))}
                          </div>
                        )}

                        <div className="pt-1">
                          <Input
                            aria-label={label}
                            value={currentDesc}
                            onChange={(e) =>
                              handleEditChange(entry.table_name, entry.column_name, e.target.value)
                            }
                            placeholder={`Add description for ${isTable ? entry.table_name : entry.column_name}…`}
                            className="text-xs h-8"
                          />
                        </div>
                      </div>
                    )
                  })}
                </div>
              </div>
            ))}
          </div>
        </div>
      )}

      {/* Definitions Section */}
      <div className="space-y-4 pt-4 border-t">
        <h3 className="text-sm font-semibold tracking-tight text-foreground uppercase text-xs">
          Business Definitions
        </h3>

        <DocumentDefinitions workspaceId={workspaceId} />

        {definitions.length > 0 && (
          <div className="space-y-2">
            {definitions.map((def) => (
              <div
                key={def.definition_id}
                data-testid="definition-row"
                className="flex items-center justify-between gap-3 rounded-lg border bg-card p-3"
              >
                <div className="space-y-1 min-w-0 flex-1">
                  <div className="flex items-center gap-2">
                    <span className="text-xs font-semibold text-foreground">{def.term}</span>
                    <Badge
                      variant="outline"
                      data-testid="source-badge"
                      className="text-[10px] font-normal"
                    >
                      {SOURCE_LABEL[def.source] ?? 'Yours'}
                    </Badge>
                  </div>
                  <p className="text-xs text-muted-foreground break-words">{def.definition}</p>
                  {def.citation && (
                    <div data-testid="definition-citation" className="space-y-0.5 pt-1 text-[11px]">
                      <blockquote className="border-l-2 border-border pl-2 italic text-muted-foreground break-words">
                        “{def.citation.quote}”
                      </blockquote>
                      <p className="text-muted-foreground">
                        {def.citation.title || 'Uploaded document'}
                        {def.citation.page != null ? `, p. ${def.citation.page}` : ''}
                      </p>
                    </div>
                  )}
                </div>

                <Button
                  type="button"
                  variant="ghost"
                  size="icon-sm"
                  aria-label={`Delete definition ${def.term}`}
                  onClick={() => handleDeleteDef(def.definition_id, def.term)}
                >
                  <Trash2 className="size-3.5 text-muted-foreground hover:text-destructive" />
                </Button>
              </div>
            ))}
          </div>
        )}

        {/* Add Definition Form */}
        <div className="space-y-3 rounded-xl border bg-muted/20 p-4">
          <h4 className="text-xs font-medium text-foreground">Add definition</h4>
          <div className="grid grid-cols-1 sm:grid-cols-2 gap-3">
            <div className="space-y-1">
              <Label htmlFor="def-term" className="text-xs">
                Term
              </Label>
              <Input
                id="def-term"
                aria-label="Term"
                value={newTerm}
                onChange={(e) => setNewTerm(e.target.value)}
                placeholder="e.g. GMV"
                className="text-xs h-8"
              />
            </div>
            <div className="space-y-1">
              <Label htmlFor="def-definition" className="text-xs">
                Definition
              </Label>
              <Input
                id="def-definition"
                aria-label="Definition"
                value={newDefinition}
                onChange={(e) => setNewDefinition(e.target.value)}
                placeholder="e.g. Price plus freight, before refunds."
                className="text-xs h-8"
              />
            </div>
          </div>
          <Button
            type="button"
            size="sm"
            onClick={handleAddDef}
            disabled={!newTerm.trim() || !newDefinition.trim() || isAddingDef}
          >
            {isAddingDef ? 'Adding…' : 'Add definition'}
          </Button>
        </div>
      </div>
    </div>
  )
}
