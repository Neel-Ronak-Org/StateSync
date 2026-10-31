import { Button } from '@/components/ui/button'
import { RUNTIME_SUGGESTIONS } from '@/config'
import { useDatasetLabel } from '@/features/library/useConnections'

/** Shown when the deployment names no suggested questions (config/suggestions.json). */
const GENERAL_SUGGESTIONS = [
  'Which tables does this database hold, and what do they describe?',
  'What do the documents in this workspace cover?',
]

interface EmptyStateProps {
  dataset: string
  onSelectSuggestion: (question: string) => void
}

export function EmptyState({ dataset, onSelectSuggestion }: EmptyStateProps) {
  const suggestions = RUNTIME_SUGGESTIONS ?? GENERAL_SUGGESTIONS
  const label = useDatasetLabel([dataset])

  return (
    <div className="flex flex-col items-start gap-4 py-8">
      <div>
        <h2 className="text-lg font-semibold tracking-tight">Ask about your data</h2>
        <p className="text-sm text-muted-foreground mt-1">
          Questions are answered from the <strong className="font-semibold text-foreground">{label(dataset)}</strong> database
          and this workspace's documents.
        </p>
      </div>
      <div className="grid w-full grid-cols-1 gap-2.5 sm:grid-cols-2 mt-2">
        {suggestions.map((suggestion) => (
          <Button
            key={suggestion}
            variant="outline"
            onClick={() => onSelectSuggestion(suggestion)}
            className="h-auto w-full justify-start whitespace-normal py-2.5 px-3 text-left text-xs font-normal leading-snug"
          >
            {suggestion}
          </Button>
        ))}
      </div>
    </div>
  )
}
