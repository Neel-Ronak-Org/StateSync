import { Database } from 'lucide-react'

import type { Workspace } from '@/api/types'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

interface WorkspacePickerProps {
  workspaces: Workspace[]
  activeWorkspaceId: string | null
  onSelectWorkspace: (workspaceId: string) => void
}

/**
 * The page header's source Select, labelled "Workspace": every question is asked in one workspace
 * (its database, its documents and what its owner approved).
 */
export function WorkspacePicker({ workspaces, activeWorkspaceId, onSelectWorkspace }: WorkspacePickerProps) {
  return (
    <Select value={activeWorkspaceId ?? undefined} onValueChange={onSelectWorkspace}>
      <SelectTrigger aria-label="Workspace" className="h-8 min-w-36 gap-2 text-xs">
        <Database className="size-3.5 text-muted-foreground" />
        <SelectValue placeholder={workspaces.length ? 'Choose a workspace' : 'No workspace yet'} />
      </SelectTrigger>
      <SelectContent align="end">
        {workspaces.map((ws) => (
          <SelectItem key={ws.workspace_id} value={ws.workspace_id} className="text-xs">
            {ws.name}
          </SelectItem>
        ))}
      </SelectContent>
    </Select>
  )
}
