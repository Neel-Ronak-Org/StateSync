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

interface BuildDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  dataset: string
  /** When the build needs a new dashboard, its name is asked for. */
  needsName: boolean
  onBuild: (goal: string, name: string) => Promise<unknown>
}

/** "Build a dashboard with AI" (web/DESIGN.md §6). */
export function BuildDialog({ open, onOpenChange, dataset, needsName, onBuild }: BuildDialogProps) {
  const [goal, setGoal] = useState('')
  const [name, setName] = useState('')
  const [starting, setStarting] = useState(false)
  const goalId = useId()
  const nameId = useId()

  useEffect(() => {
    if (open) {
      setGoal('')
      setName(needsName ? 'AI insights' : '')
    }
  }, [open, needsName])

  const submit = async (e: FormEvent) => {
    e.preventDefault()
    if (starting || (needsName && !name.trim())) return
    setStarting(true)
    try {
      await onBuild(goal.trim(), name.trim())
      onOpenChange(false)
    } finally {
      setStarting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <form onSubmit={submit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Build a dashboard with AI</DialogTitle>
            <DialogDescription>
              StateSync plans a set of insights for the <span className="font-medium text-foreground">{dataset}</span>{' '}
              dataset, answers each one through the normal pipeline, and adds the ones that work. It takes a minute
              or two; cards appear as they are ready.
            </DialogDescription>
          </DialogHeader>
          <div className="space-y-2">
            <Label htmlFor={goalId}>Focus (optional)</Label>
            <Input
              id={goalId}
              value={goal}
              onChange={(e) => setGoal(e.target.value)}
              placeholder="e.g. delivery performance"
              maxLength={200}
              autoFocus
            />
          </div>
          {needsName && (
            <div className="space-y-2">
              <Label htmlFor={nameId}>Dashboard name</Label>
              <Input id={nameId} value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
            </div>
          )}
          <DialogFooter>
            <Button type="button" variant="outline" onClick={() => onOpenChange(false)}>Cancel</Button>
            <Button type="submit" disabled={starting || (needsName && !name.trim())}>Build</Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
