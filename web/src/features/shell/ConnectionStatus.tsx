import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
import { useHealth } from '@/hooks/useHealth'
import { cn } from '@/lib/utils'

type Status = 'ok' | 'degraded' | 'down' | 'unknown'

/** The rail's status light (web/DESIGN.md §4): server reachable, and model ready. */
export function ConnectionStatus() {
  const { data, isError } = useHealth()
  const status: Status = isError ? 'down' : !data ? 'unknown' : data.llm_ready ? 'ok' : 'degraded'
  const text =
    status === 'ok'
      ? 'Connected'
      : status === 'degraded'
        ? `Model unavailable${data?.llm_detail ? `: ${data.llm_detail}` : ''}`
        : status === 'down'
          ? 'Server unreachable'
          : 'Checking connection'

  return (
    <Tooltip>
      <TooltipTrigger asChild>
        <div
          data-testid="connection-status"
          data-status={status}
          role="status"
          tabIndex={0}
          className="grid size-10 place-items-center rounded-xl outline-none focus-visible:ring-2 focus-visible:ring-ring"
        >
          <span
            className={cn(
              'size-2.5 rounded-full ring-4 transition-colors',
              status === 'ok' && 'bg-success ring-success/15',
              status === 'degraded' && 'bg-amber-400 ring-amber-400/15',
              status === 'down' && 'bg-destructive ring-destructive/15',
              status === 'unknown' && 'bg-muted-foreground ring-muted-foreground/15',
            )}
          />
          <span className="sr-only">{text}</span>
        </div>
      </TooltipTrigger>
      <TooltipContent side="right">{text}</TooltipContent>
    </Tooltip>
  )
}
