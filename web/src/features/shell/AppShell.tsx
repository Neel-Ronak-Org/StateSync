import { useState, type ReactNode } from 'react'
import { Menu } from 'lucide-react'

import type { SessionSummary } from '@/api/types'
import { Button } from '@/components/ui/button'
import { Sheet, SheetContent, SheetTitle } from '@/components/ui/sheet'
import { Brand } from '@/features/shell/Brand'
import { Sidebar } from '@/features/shell/Sidebar'

interface AppShellProps {
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
  children: ReactNode
}

export function AppShell({
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
  children,
}: AppShellProps) {
  const [mobileOpen, setMobileOpen] = useState(false)

  const handleNavigate = (route: 'chat' | 'dashboard') => {
    onNavigate(route)
    setMobileOpen(false)
  }

  const handleNewChat = () => {
    onNewChat()
    setMobileOpen(false)
  }

  const handleSelectSession = (sessionId: string) => {
    onSelectSession(sessionId)
    setMobileOpen(false)
  }

  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      {/* Desktop sidebar */}
      <div className="hidden md:flex md:w-64 md:flex-col md:shrink-0">
        <Sidebar
          currentRoute={currentRoute}
          onNavigate={handleNavigate}
          onNewChat={handleNewChat}
          sessions={sessions}
          activeSessionId={activeSessionId}
          onSelectSession={handleSelectSession}
          dataset={dataset}
          availableSchemas={availableSchemas}
          onSelectDataset={onSelectDataset}
          devMode={devMode}
          onToggleDevMode={onToggleDevMode}
          onSignOut={onSignOut}
        />
      </div>

      {/* Mobile sheet sidebar */}
      <Sheet open={mobileOpen} onOpenChange={setMobileOpen}>
        <SheetContent side="left" className="p-0 w-64">
          <SheetTitle className="sr-only">Navigation menu</SheetTitle>
          <Sidebar
            currentRoute={currentRoute}
            onNavigate={handleNavigate}
            onNewChat={handleNewChat}
            sessions={sessions}
            activeSessionId={activeSessionId}
            onSelectSession={handleSelectSession}
            dataset={dataset}
            availableSchemas={availableSchemas}
            onSelectDataset={onSelectDataset}
            devMode={devMode}
            onToggleDevMode={onToggleDevMode}
            onSignOut={onSignOut}
            className="border-r-0"
          />
        </SheetContent>
      </Sheet>

      {/* Main content area */}
      <div className="flex flex-1 flex-col min-w-0 overflow-hidden">
        {/* Mobile top bar with Open menu button */}
        <div className="flex md:hidden h-14 items-center justify-between border-b px-4 shrink-0 bg-sidebar">
          <div className="flex items-center gap-2">
            <Button
              variant="ghost"
              size="icon-sm"
              aria-label="Open menu"
              onClick={() => setMobileOpen(true)}
            >
              <Menu className="size-4" />
            </Button>
            <Brand />
          </div>
        </div>

        {/* Page content */}
        <div className="flex-1 flex flex-col min-h-0 overflow-hidden">{children}</div>
      </div>
    </div>
  )
}
