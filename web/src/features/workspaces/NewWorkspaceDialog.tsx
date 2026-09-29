import { useState } from 'react'

import { ApiError } from '@/api/client'
import type { Workspace } from '@/api/types'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Textarea } from '@/components/ui/textarea'
import { useConnections } from '@/features/library/useConnections'

interface NewWorkspaceDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  availableSchemas: string[]
  onCreateWorkspace: (data: {
    name: string
    target_schema: string
    business_context?: string
  }) => Promise<{ workspace: Workspace }>
  onCreated: (workspace: Workspace) => void
}

/** Dialog for creating a new workspace. */
export function NewWorkspaceDialog({
  open,
  onOpenChange,
  availableSchemas,
  onCreateWorkspace,
  onCreated,
}: NewWorkspaceDialogProps) {
  const [name, setName] = useState('')
  const [dataset, setDataset] = useState(() => availableSchemas[0] ?? 'public')
  const [about, setAbout] = useState('')
  const [isSubmitting, setIsSubmitting] = useState(false)
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  // A connected database with a chosen schema is a dataset; its id is the target_schema.
  const yourDatabases = useConnections(open).connections.filter((c) => c.schema_name)

  const handleOpenChange = (nextOpen: boolean) => {
    if (!nextOpen) {
      setName('')
      setAbout('')
      setErrorMessage(null)
    }
    onOpenChange(nextOpen)
  }

  const handleCreate = async () => {
    if (!name.trim() || isSubmitting) return
    setIsSubmitting(true)
    setErrorMessage(null)
    try {
      const res = await onCreateWorkspace({
        name: name.trim(),
        target_schema: dataset,
        business_context: about.trim() || undefined,
      })
      handleOpenChange(false)
      onCreated(res.workspace)
    } catch (err) {
      setErrorMessage(err instanceof ApiError ? err.message : 'Failed to create workspace.')
    } finally {
      setIsSubmitting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={handleOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>New workspace</DialogTitle>
        </DialogHeader>

        <form
          onSubmit={(e) => {
            e.preventDefault()
            void handleCreate()
          }}
          className="space-y-4 py-2"
        >
          {errorMessage && (
            <Alert variant="destructive">
              <AlertDescription>{errorMessage}</AlertDescription>
            </Alert>
          )}

          <div className="space-y-1.5">
            <Label htmlFor="ws-name">Name</Label>
            <Input
              id="ws-name"
              value={name}
              onChange={(e) => setName(e.target.value)}
              placeholder="e.g. Furniture shop"
              autoFocus
            />
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ws-dataset">Dataset</Label>
            <Select value={dataset} onValueChange={setDataset}>
              <SelectTrigger id="ws-dataset" aria-label="Dataset">
                <SelectValue placeholder="Dataset" />
              </SelectTrigger>
              <SelectContent>
                {availableSchemas.map((schema) => (
                  <SelectItem key={schema} value={schema}>
                    {schema}
                  </SelectItem>
                ))}
                {yourDatabases.length > 0 && (
                  <SelectGroup>
                    <SelectLabel>Your databases</SelectLabel>
                    {yourDatabases.map((c) => (
                      <SelectItem key={c.connection_id} value={c.connection_id}>
                        {c.name}
                      </SelectItem>
                    ))}
                  </SelectGroup>
                )}
              </SelectContent>
            </Select>
          </div>

          <div className="space-y-1.5">
            <Label htmlFor="ws-about">About your business</Label>
            <Textarea
              id="ws-about"
              value={about}
              onChange={(e) => setAbout(e.target.value)}
              placeholder="Describe what your organization does and how you use this data."
              rows={3}
            />
            <p className="text-xs text-muted-foreground">
              Optional. Helps the AI understand your schema and generates better answers.
            </p>
          </div>

          <DialogFooter className="pt-2">
            <Button
              type="button"
              variant="outline"
              onClick={() => handleOpenChange(false)}
              disabled={isSubmitting}
            >
              Cancel
            </Button>
            <Button
              type="submit"
              disabled={!name.trim() || isSubmitting}
            >
              {isSubmitting ? 'Creating…' : 'Create'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
