import { Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { ApiError } from '@/api/client'
import type { ExampleInfo } from '@/api/types'
import { Button } from '@/components/ui/button'

interface ApprovedAnswersTabProps {
  examples: ExampleInfo[]
  onRemoveExample: (question: string) => Promise<unknown>
}

/**
 * The workspace's approved answers: questions whose SQL the owner marked correct (thumbs-up in Ask, or
 * pinning an answer). The same question asked again runs that SQL as is and is labelled "Verified".
 */
export function ApprovedAnswersTab({ examples, onRemoveExample }: ApprovedAnswersTabProps) {
  const handleRemove = async (question: string) => {
    try {
      await onRemoveExample(question)
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'Failed to remove the approved answer.')
    }
  }

  return (
    <div className="space-y-4 py-4">
      <p className="text-xs text-muted-foreground">
        Asking one of these questions again runs its approved SQL directly, with no model call, and the answer
        is labelled Verified. Similar questions show the approved SQL to the SQL writer as an example.
      </p>
      {examples.length === 0 ? (
        <p className="rounded-xl border bg-card p-4 text-xs text-muted-foreground">
          No approved answers yet. Mark an answer correct in Ask to add one.
        </p>
      ) : (
        <div className="space-y-2">
          {examples.map((example) => (
            <div
              key={example.example_id}
              data-testid="approved-answer"
              className="flex items-start justify-between gap-3 rounded-xl border bg-card p-3"
            >
              <div className="min-w-0 flex-1 space-y-1">
                <p className="text-sm font-medium text-foreground">{example.question}</p>
                <p className="text-xs text-muted-foreground font-mono break-words">{example.sql_text}</p>
              </div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                aria-label={`Remove approved answer ${example.question}`}
                onClick={() => handleRemove(example.question)}
                className="h-7 gap-1.5 text-xs"
              >
                <Trash2 className="size-3.5" />
                Remove
              </Button>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}
