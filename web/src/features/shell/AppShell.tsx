import type { ReactNode } from 'react'
import { AnimatePresence, motion } from 'motion/react'

import { Rail } from '@/features/shell/Rail'
import type { AppRoute } from '@/lib/route'

interface AppShellProps {
  route: AppRoute
  onNavigate: (route: AppRoute) => void
  devMode: boolean
  onToggleDevMode: (enabled: boolean) => void
  onSignOut: () => void
  children: ReactNode
}

/** Rail + the current page, which glides in when the route changes (web/DESIGN.md §3). */
export function AppShell({ route, onNavigate, devMode, onToggleDevMode, onSignOut, children }: AppShellProps) {
  return (
    <div className="flex h-screen w-full overflow-hidden bg-background">
      <Rail
        route={route}
        onNavigate={onNavigate}
        devMode={devMode}
        onToggleDevMode={onToggleDevMode}
        onSignOut={onSignOut}
      />
      <AnimatePresence mode="wait" initial={false}>
        <motion.div
          key={route}
          className="flex min-w-0 flex-1 flex-col overflow-hidden"
          initial={{ opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          exit={{ opacity: 0, y: -4 }}
          transition={{ duration: 0.2, ease: 'easeOut' }}
        >
          {children}
        </motion.div>
      </AnimatePresence>
    </div>
  )
}
