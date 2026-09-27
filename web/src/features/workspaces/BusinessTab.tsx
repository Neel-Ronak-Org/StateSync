import { useEffect, useState } from 'react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import type { Workspace } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Label } from '@/components/ui/label'
import { Textarea } from '@/components/ui/textarea'

interface BusinessTabProps {
  workspace: Workspace
  onUpdateWorkspace: (body: { name?: string; business_context?: string }) => Promise<unknown>
}

/** The Business step tab where the owner describes their business context. */
export function BusinessTab({ workspace, onUpdateWorkspace }: BusinessTabProps) {
  const [context, setContext] = useState(workspace.business_context)
  const [isSaving, setIsSaving] = useState(false)

  useEffect(() => {
    setContext(workspace.business_context)
  }, [workspace.business_context])

  const handleSave = async () => {
    setIsSaving(true)
    try {
      await onUpdateWorkspace({ business_context: context })
      toast.success('Business context saved')
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to save business context.')
    } finally {
      setIsSaving(false)
    }
  }

  return (
    <div className="space-y-6 max-w-2xl py-4">
      <div className="space-y-2">
        <div className="flex items-center justify-between">
          <Label htmlFor="business-context" className="text-sm font-medium">
            About your business
          </Label>
          <span className="text-xs text-muted-foreground">
            Version {workspace.context_version}
          </span>
        </div>
        <Textarea
          id="business-context"
          aria-label="About your business"
          value={context}
          onChange={(e) => setContext(e.target.value)}
          placeholder="Describe your company, terminology, key metrics, and domain rules."
          rows={8}
          className="resize-y"
        />
        <p className="text-xs text-muted-foreground">
          StateSync reads this, as your own words, whenever it writes a query in this workspace. Say what the
          business does and what your terms mean (for example, what counts as revenue).
        </p>
      </div>

      <Button onClick={handleSave} disabled={isSaving || context.trim() === workspace.business_context}>
        Save
      </Button>
    </div>
  )
}
