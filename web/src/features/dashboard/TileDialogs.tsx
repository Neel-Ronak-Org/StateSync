import { useEffect, useState } from 'react'
import { Copy } from 'lucide-react'
import { toast } from 'sonner'

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
import { RadioGroup, RadioGroupItem } from '@/components/ui/radio-group'
import { copyToClipboard } from '@/lib/clipboard'

interface RenameTileDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  initialTitle: string
  onSave: (title: string) => Promise<unknown>
}

export function RenameTileDialog({
  open,
  onOpenChange,
  initialTitle,
  onSave,
}: RenameTileDialogProps) {
  const [title, setTitle] = useState(initialTitle)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open) setTitle(initialTitle)
  }, [open, initialTitle])

  const handleSubmit = async (e: React.FormEvent) => {
    e.preventDefault()
    if (!title.trim() || isSaving) return
    setIsSaving(true)
    try {
      await onSave(title.trim())
      onOpenChange(false)
    } catch {
      // Error is handled/toasted by caller
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <form onSubmit={handleSubmit} className="space-y-4">
          <DialogHeader>
            <DialogTitle>Rename tile</DialogTitle>
          </DialogHeader>
          <div className="space-y-2 py-2">
            <Label htmlFor="rename-tile-title">Tile title</Label>
            <Input
              id="rename-tile-title"
              value={title}
              onChange={(e) => setTitle(e.target.value)}
              autoFocus
            />
          </div>
          <DialogFooter className="gap-2 sm:gap-0">
            <Button
              type="button"
              variant="outline"
              onClick={() => onOpenChange(false)}
            >
              Cancel
            </Button>
            <Button type="submit" disabled={isSaving || !title.trim()}>
              Save
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}

const INTERVAL_OPTIONS = [
  { label: 'Every 15 minutes', seconds: 900 },
  { label: 'Every hour', seconds: 3600 },
  { label: 'Every 6 hours', seconds: 21600 },
  { label: 'Every day', seconds: 86400 },
]

interface RefreshIntervalDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  currentInterval: number
  onSave: (seconds: number) => Promise<unknown>
}

export function RefreshIntervalDialog({
  open,
  onOpenChange,
  currentInterval,
  onSave,
}: RefreshIntervalDialogProps) {
  const [selected, setSelected] = useState<string>(String(currentInterval))
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    if (open) setSelected(String(currentInterval))
  }, [open, currentInterval])

  const handleSave = async () => {
    setIsSaving(true)
    try {
      await onSave(Number(selected))
      onOpenChange(false)
    } catch {
      // Error is handled/toasted by caller
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Refresh interval</DialogTitle>
        </DialogHeader>
        <div className="py-2">
          <RadioGroup value={selected} onValueChange={setSelected} className="gap-3">
            {INTERVAL_OPTIONS.map((opt) => {
              const id = `interval-${opt.seconds}`
              return (
                <div key={opt.seconds} className="flex items-center gap-2">
                  <RadioGroupItem value={String(opt.seconds)} id={id} />
                  <Label htmlFor={id} className="text-sm font-normal cursor-pointer">
                    {opt.label}
                  </Label>
                </div>
              )
            })}
          </RadioGroup>
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button type="button" onClick={handleSave} disabled={isSaving}>
            Save
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface UnpinTileDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  onUnpin: () => Promise<unknown>
}

export function UnpinTileDialog({
  open,
  onOpenChange,
  onUnpin,
}: UnpinTileDialogProps) {
  const [isDeleting, setIsDeleting] = useState(false)

  const handleUnpin = async () => {
    setIsDeleting(true)
    try {
      await onUnpin()
      onOpenChange(false)
    } catch {
      // Error is handled/toasted by caller
    } finally {
      setIsDeleting(false)
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Unpin this tile?</DialogTitle>
          <DialogDescription className="text-sm text-muted-foreground pt-1">
            It stops refreshing and is removed from the dashboard. The conversation it came from is kept.
          </DialogDescription>
        </DialogHeader>
        <DialogFooter className="gap-2 sm:gap-0 mt-2">
          <Button
            type="button"
            variant="outline"
            onClick={() => onOpenChange(false)}
          >
            Cancel
          </Button>
          <Button
            type="button"
            variant="destructive"
            onClick={handleUnpin}
            disabled={isDeleting}
          >
            Unpin
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}

interface TileSqlDialogProps {
  open: boolean
  onOpenChange: (open: boolean) => void
  sql: string
}

export function TileSqlDialog({
  open,
  onOpenChange,
  sql,
}: TileSqlDialogProps) {
  const handleCopy = async () => {
    const ok = await copyToClipboard(sql)
    if (ok) {
      toast.success('SQL copied to clipboard')
    } else {
      toast.error('Copy failed. Select the text and copy it manually.')
    }
  }

  return (
    <Dialog open={open} onOpenChange={onOpenChange}>
      <DialogContent className="sm:max-w-lg">
        <DialogHeader>
          <DialogTitle>Tile SQL</DialogTitle>
        </DialogHeader>
        <div className="py-2">
          <pre className="font-mono text-xs bg-muted rounded-md p-3 overflow-auto max-h-96">
            {sql}
          </pre>
        </div>
        <DialogFooter className="gap-2 sm:gap-0">
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={handleCopy}
            className="gap-1.5"
          >
            <Copy className="size-3.5" />
            Copy
          </Button>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onOpenChange(false)}
          >
            Close
          </Button>
        </DialogFooter>
      </DialogContent>
    </Dialog>
  )
}
