import { useEffect, useId, useState, type FormEvent } from 'react'

import { Button } from '@/components/ui/button'
import {
  Dialog,
  DialogContent,
  DialogDescription,
  DialogFooter,
  DialogHeader,
  DialogTitle,
} from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'

interface NameDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  title: string
  submitLabel: string
  initialName: string
  onSubmit: (name: string) => Promise<unknown>
}

/** "New dashboard" and "Rename dashboard": one input labelled "Dashboard name". */
export function DashboardNameDialog({ open, onOpenChange, title, submitLabel, initialName, onSubmit }: NameDialogProps) {
  const [name, setName] = useState(initialName)
  const [saving, setSaving] = useState(false)
  const id = useId()

  useEffect(() => {
    if (open) setName(initialName)
  }, [open, initialName])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (!name.trim() || saving) return
    setSaving(true)
    try {
      await onSubmit(name.trim())
      onOpenChange(false)
    } catch {
      // the caller has already said why
    } finally {
      setSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>{title}</DialogTitle>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={id}>Dashboard name</Label>
            <Input id={id} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} autoFocus />
          </div>
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={saving || !name.trim()}>{submitLabel}</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

interface DeleteDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  name: string
  tileCount: number
  onDelete: () => Promise<unknown>
}

export function DeleteDashboardDialog({ open, onOpenChange, name, tileCount, onDelete }: DeleteDialogProps) {
  const [deleting, setDeleting] = useState(false)
  const confirm = async () => {
    setDeleting(true)
    try {
      await onDelete()
      onOpenChange(false)
    } catch {
      // the caller has already said why
    } finally {
      setDeleting(false)
    }
  }
  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Delete this dashboard?</DialogTitle>
          <DialogDescription>
            “{name}” and its {tileCount === 1 ? '1 tile' : `${tileCount} tiles`} are removed. The conversations
            they came from are kept.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter>
          <Button variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
          <Button variant="destructive" onClick={() => void confirm()} disabled={deleting}>Delete</Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
