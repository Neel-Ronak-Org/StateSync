import { LayoutDashboard, LogOut, MessageSquare, Plus, SunMoon } from 'lucide-react'

import type { SessionSummary } from '@/api/types'
import { Button } from '@/components/ui/button'
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Label } from '@/components/ui/label'
import {
  Select,
  SelectContent,
  SelectItem,
  SelectTrigger,
  SelectValue,
} from '@/components/ui/select'
import { Switch } from '@/components/ui/switch'
import { type ThemeChoice, useTheme } from '@/components/theme-provider'
import { Brand } from '@/features/shell/Brand'
import { formatRelativeTime } from '@/lib/format'
import { cn } from '@/lib/utils'

interface SidebarProps {
  currentRoute: 'chat' | 'dashboard'
  onNavigate: (route: 'chat' | 'dashboard') => void
  onNewChat: () => void
  sessions: SessionSummary[]
  activeSessionId: string
  onSelectSession: (sessionId: string) => void
  dataset: string
  availableSchemas: string[]
  onSelectDataset: (dataset: string) => void
  devMode: boolean
  onToggleDevMode: (devMode: boolean) => void
  onSignOut: () => void
  className?: string
}

export function Sidebar({
  currentRoute,
  onNavigate,
  onNewChat,
  sessions,
  activeSessionId,
  onSelectSession,
  dataset,
  availableSchemas,
  onSelectDataset,
  devMode,
  onToggleDevMode,
  onSignOut,
  className,
}: SidebarProps) {
  const { theme, setTheme } = useTheme()

  return (
    <aside
      className={cn(
        'flex flex-col h-full bg-sidebar border-r text-sidebar-foreground select-none',
        className
      )}
    >
      {/* Brand row */}
      <div className="flex h-14 items-center px-4 border-b">
        <Brand />
      </div>

      {/* New chat button */}
      <div className="p-3">
        <Button
          variant="outline"
          className="w-full justify-start gap-2 h-8 text-xs font-medium border-border"
          onClick={onNewChat}
        >
          <Plus className="size-3.5" />
          <span>New chat</span>
        </Button>
      </div>

      {/* Nav */}
      <div className="px-3 space-y-1">
        <button
          type="button"
          aria-current={currentRoute === 'chat' ? 'page' : undefined}
          onClick={() => onNavigate('chat')}
          className={cn(
            'flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-xs font-medium transition-colors relative text-left',
            currentRoute === 'chat'
              ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
              : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
          )}
        >
          {currentRoute === 'chat' && (
            <div className="absolute left-0 top-1.5 bottom-1.5 w-[2px] bg-brand rounded-r" />
          )}
          <MessageSquare className="size-3.5" />
          <span>Chat</span>
        </button>

        <button
          type="button"
          aria-current={currentRoute === 'dashboard' ? 'page' : undefined}
          onClick={() => onNavigate('dashboard')}
          className={cn(
            'flex h-8 w-full items-center gap-2 rounded-md px-2.5 text-xs font-medium transition-colors relative text-left',
            currentRoute === 'dashboard'
              ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
              : 'text-muted-foreground hover:bg-sidebar-accent/50 hover:text-sidebar-foreground'
          )}
        >
          {currentRoute === 'dashboard' && (
            <div className="absolute left-0 top-1.5 bottom-1.5 w-[2px] bg-brand rounded-r" />
          )}
          <LayoutDashboard className="size-3.5" />
          <span>Dashboard</span>
        </button>
      </div>

      {/* Recent conversations */}
      <div className="flex-1 flex flex-col min-h-0 pt-4 px-3">
        <span className="text-xs font-medium text-muted-foreground px-1 pb-1.5">Recent</span>
        <nav
          aria-label="Recent conversations"
          className="flex-1 overflow-y-auto space-y-1 -mx-1 px-1"
        >
          {sessions.map((s) => {
            const isActive = s.session_id === activeSessionId
            const { relative, absolute } = formatRelativeTime(s.last_activity || s.started_at)
            return (
              <button
                key={s.session_id}
                type="button"
                aria-label={s.first_query}
                title={s.first_query}
                onClick={() => onSelectSession(s.session_id)}
                className={cn(
                  'w-full text-left p-2 rounded-md transition-colors relative flex flex-col gap-0.5 group',
                  isActive
                    ? 'bg-sidebar-accent font-medium text-sidebar-accent-foreground'
                    : 'hover:bg-sidebar-accent/50 text-muted-foreground hover:text-sidebar-foreground'
                )}
              >
                {isActive && (
                  <div className="absolute left-0 top-1.5 bottom-1.5 w-[2px] bg-brand rounded-r" />
                )}
                <span className="truncate text-xs font-medium text-sidebar-foreground block w-full">
                  {s.first_query}
                </span>
                <span className="text-[11px] text-muted-foreground block" title={absolute}>
                  {relative}
                </span>
              </button>
            )
          })}
        </nav>
      </div>

      {/* Footer */}
      <div className="p-3 border-t space-y-3 shrink-0">
        {/* Dataset Select */}
        <div className="space-y-1">
          <Label className="text-[11px] text-muted-foreground font-medium">Dataset</Label>
          <Select value={dataset} onValueChange={onSelectDataset}>
            <SelectTrigger aria-label="Dataset" className="w-full h-8 text-xs">
              <SelectValue placeholder="Dataset" />
            </SelectTrigger>
            <SelectContent>
              {availableSchemas.map((schema) => (
                <SelectItem key={schema} value={schema} className="text-xs">
                  {schema}
                </SelectItem>
              ))}
            </SelectContent>
          </Select>
        </div>

        {/* Developer mode Switch */}
        <div className="flex items-center justify-between py-1">
          <Label htmlFor="developer-mode-switch" className="text-xs font-normal cursor-pointer">
            Developer mode
          </Label>
          <Switch
            id="developer-mode-switch"
            aria-label="Developer mode"
            checked={devMode}
            onCheckedChange={onToggleDevMode}
          />
        </div>

        {/* Theme and Sign out */}
        <div className="flex items-center justify-between pt-1 border-t">
          <DropdownMenu>
            <DropdownMenuTrigger asChild>
              <Button variant="ghost" size="icon-sm" aria-label="Theme" className="h-7 w-7">
                <SunMoon className="size-4" />
              </Button>
            </DropdownMenuTrigger>
            <DropdownMenuContent align="start">
              <DropdownMenuRadioGroup
                value={theme}
                onValueChange={(val) => setTheme(val as ThemeChoice)}
              >
                <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
                <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
              </DropdownMenuRadioGroup>
            </DropdownMenuContent>
          </DropdownMenu>

          <Button
            variant="ghost"
            size="sm"
            onClick={onSignOut}
            className="h-7 gap-1.5 text-xs text-muted-foreground hover:text-foreground px-2"
          >
            <LogOut className="size-3.5" />
            <span>Sign out</span>
          </Button>
        </div>
      </div>
    </aside>
  )
}
