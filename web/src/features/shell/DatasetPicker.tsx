import { Database } from 'lucide-react'

import type { Workspace } from '@/api/types'
import {
  Select,
  SelectContent,
  SelectGroup,
  SelectItem,
  SelectLabel,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'

interface DatasetPickerProps {
  dataset: string
  available: string[]
  live: string[]
  workspaces?: Workspace[]
  /** The active workspace, only once it is in `workspaces` (else the dataset shows). */
  activeWorkspaceId?: string | null
  onSelectDataset: (dataset: string) => void
  onSelectWorkspace?: (workspaceId: string) => void
}

/**
 * The page header's source Select (web/DESIGN.md §4), labelled "Dataset": the
 * sample datasets, then my workspaces (Phase 3). Values are 'ds:<schema>' or
 * 'ws:<workspace_id>'.
 */
export function DatasetPicker({
  dataset,
  available,
  live,
  workspaces,
  activeWorkspaceId,
  onSelectDataset,
  onSelectWorkspace,
}: DatasetPickerProps) {
  const currentValue = activeWorkspaceId ? `ws:${activeWorkspaceId}` : `ds:${dataset}`

  const handleValueChange = (val: string) => {
    if (val.startsWith('ws:')) onSelectWorkspace?.(val.slice(3))
    else if (val.startsWith('ds:')) onSelectDataset(val.slice(3))
  }

  return (
    <Select value={currentValue} onValueChange={handleValueChange}>
      <SelectTrigger aria-label="Dataset" className="h-8 min-w-36 gap-2 text-xs">
        <Database className="size-3.5 text-muted-foreground" />
        <SelectValue placeholder="Dataset" />
      </SelectTrigger>
      <SelectContent align="end">
        {available.map((schema) => (
          <SelectItem key={`ds:${schema}`} value={`ds:${schema}`} className="text-xs">
            <span>{schema}</span>
            {live.includes(schema) && (
              <span className="ml-2 rounded-full bg-primary/15 px-1.5 py-px text-[10px] font-medium text-primary">
                Live
              </span>
            )}
          </SelectItem>
        ))}
        {workspaces && workspaces.length > 0 && (
          <SelectGroup>
            <SelectLabel>Workspaces</SelectLabel>
            {workspaces.map((ws) => (
              <SelectItem key={`ws:${ws.workspace_id}`} value={`ws:${ws.workspace_id}`} className="text-xs">
                {ws.name}
              </SelectItem>
            ))}
          </SelectGroup>
        )}
      </SelectContent>
    </Select>
  )
}
