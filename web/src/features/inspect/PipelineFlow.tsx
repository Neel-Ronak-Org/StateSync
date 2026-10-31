import { ChevronRight, Database, FileSearch, ListOrdered, PenLine, Split } from 'lucide-react'

import { formatMs, type PathRoute, type StepId } from '@/features/inspect/inspect'
import { cn } from '@/lib/utils'

interface PipelineFlowProps {
  route: PathRoute
  /** The steps that ran, in order (inspect.stepsFor). */
  steps: StepId[]
  /** Time spent in each step, when known. */
  times: Partial<Record<StepId, number>>
  /** What the re-ranking step used, e.g. "bge + Jev". */
  rerankLabel: string
  routerLabel: string
  onSelect: (step: StepId) => void
}

const ROUTE_LABEL: Record<PathRoute, string> = {
  sql: 'Database', docs: 'Documents', hybrid: 'Database + documents', neither: 'Neither source',
}

function Node({
  id, title, subtitle, icon: Icon, active, time, onSelect,
}: {
  id: StepId
  title: string
  subtitle: string
  icon: typeof Database
  active: boolean
  time?: number
  onSelect: (step: StepId) => void
}) {
  return (
    <button
      type="button"
      data-testid={`flow-node-${id}`}
      data-active={active}
      disabled={!active}
      onClick={() => onSelect(id)}
      className={cn(
        'flex min-w-0 flex-1 items-center gap-2 rounded-lg border px-3 py-2 text-left transition-colors',
        active
          ? 'border-primary/50 bg-primary/10 hover:bg-primary/15 cursor-pointer'
          : 'border-dashed bg-transparent opacity-45',
      )}
    >
      <Icon className={cn('size-4 shrink-0', active ? 'text-primary' : 'text-muted-foreground')} />
      <span className="min-w-0 flex-1">
        <span className="block truncate text-xs font-medium text-foreground">{title}</span>
        <span className="block truncate text-[11px] text-muted-foreground">{active ? subtitle : 'not used'}</span>
      </span>
      {active && time != null && (
        <span className="shrink-0 font-mono text-[11px] text-muted-foreground">{formatMs(time)}</span>
      )}
    </button>
  )
}

function Arrow({ active }: { active: boolean }) {
  return <ChevronRight className={cn('size-4 shrink-0', active ? 'text-primary' : 'text-muted-foreground/40')} />
}

/** The pipeline as a diagram: router, then the database lane and the documents lane, then the answer writer. */
export function PipelineFlow({ route, steps, times, rerankLabel, routerLabel, onSelect }: PipelineFlowProps) {
  const ran = (s: StepId) => steps.includes(s)
  const dbLane = ran('sql')
  const docsLane = ran('search')

  return (
    <div data-testid="pipeline-flow" className="rounded-xl border bg-muted/30 p-3">
      <div className="mb-2 flex items-center gap-2 text-xs text-muted-foreground">
        <Split className="size-3.5" />
        <span>
          Route taken: <span className="font-medium text-foreground">{ROUTE_LABEL[route]}</span>. Click a step to jump to it.
        </span>
      </div>
      <div className="flex items-center gap-2">
        <div className="w-44 shrink-0">
          <Node id="router" title="Router" subtitle={routerLabel} icon={Split} active time={times.router} onSelect={onSelect} />
        </div>
        <Arrow active />
        <div className="flex min-w-0 flex-1 flex-col gap-2">
          <div className="flex items-center gap-2">
            <Node id="sql" title="Database" subtitle="write, check and run SQL" icon={Database} active={dbLane} time={times.sql} onSelect={onSelect} />
          </div>
          <div className="flex items-center gap-2">
            <Node id="search" title="Documents search" subtitle="two rounds" icon={FileSearch} active={docsLane} time={times.search} onSelect={onSelect} />
            <Arrow active={ran('rerank')} />
            <Node id="rerank" title="Re-ranking" subtitle={rerankLabel} icon={ListOrdered} active={ran('rerank')} time={times.rerank} onSelect={onSelect} />
          </div>
        </div>
        <Arrow active />
        <div className="w-44 shrink-0">
          <Node
            id="writer"
            title="Answer writer"
            subtitle={route === 'neither' ? 'fixed reply, no model' : 'LLM writes the answer'}
            icon={PenLine}
            active
            time={times.writer}
            onSelect={onSelect}
          />
        </div>
      </div>
    </div>
  )
}
