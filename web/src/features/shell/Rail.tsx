import { Boxes, Code2, Database, LayoutDashboard, LogOut, MessageSquare, SunMoon, type LucideIcon } from 'lucide-react'
import { motion } from 'motion/react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { type ThemeChoice, useTheme } from '@/components/theme-provider'
import { BrandMark } from '@/features/shell/Brand'
import { ConnectionStatus } from '@/features/shell/ConnectionStatus'
import type { AppRoute } from '@/lib/route'
import { cn } from '@/lib/utils'

interface RailProps {
  route: AppRoute
  onNavigate: (route: AppRoute) => void
  devMode: boolean
  onToggleDevMode: (enabled: boolean) => void
  onSignOut: () => void
}

interface NavItem {
  route: AppRoute
  label: string
  hint: string
  icon: LucideIcon
}

// Two groups, in the order an owner meets them: use the system, and set up what it answers from.
const NAV: { group: string; items: NavItem[] }[] = [
  {
    group: 'Use',
    items: [
      { route: 'ask', label: 'Ask', hint: 'Questions and answers', icon: MessageSquare },
      { route: 'dashboards', label: 'Dashboards', hint: 'Pinned answers', icon: LayoutDashboard },
    ],
  },
  {
    group: 'Set up',
    items: [
      { route: 'workspaces', label: 'Workspaces', hint: 'One per business', icon: Boxes },
      { route: 'library', label: 'Data sources', hint: 'Databases and documents', icon: Database },
    ],
  },
]

const rowButton =
  'relative flex h-9 w-full items-center gap-3 rounded-lg px-3 text-sm text-muted-foreground outline-none transition-colors ' +
  'hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring'

/** The navigation sidebar (web/DESIGN.md §4): labelled, grouped into using the system and setting it up. */
export function Rail({ route, onNavigate, devMode, onToggleDevMode, onSignOut }: RailProps) {
  const { theme, setTheme } = useTheme()

  return (
    <aside className="flex w-16 shrink-0 flex-col border-r bg-sidebar px-2 py-3 md:w-56 md:px-3">
      <button
        type="button"
        aria-label="StateSync home"
        onClick={() => onNavigate('ask')}
        className="flex items-center gap-2.5 rounded-lg px-2 py-1.5 outline-none focus-visible:ring-2 focus-visible:ring-ring"
      >
        <BrandMark className="size-7" />
        <span className="hidden text-sm font-semibold tracking-tight text-foreground md:inline">StateSync</span>
      </button>

      <nav aria-label="Main" className="mt-6 flex flex-col gap-5">
        {NAV.map(({ group, items }) => (
          <div key={group} className="flex flex-col gap-1">
            <div className="hidden px-3 pb-1 text-[11px] font-medium uppercase tracking-wider text-muted-foreground/70 md:block">
              {group}
            </div>
            {items.map(({ route: target, label, hint, icon: Icon }) => {
              const active = route === target
              return (
                <button
                  key={target}
                  type="button"
                  aria-label={label}
                  title={hint}
                  aria-current={active ? 'page' : undefined}
                  onClick={() => onNavigate(target)}
                  className={cn(rowButton, active && 'text-primary hover:bg-transparent hover:text-primary')}
                >
                  {active && (
                    <motion.span
                      layoutId="rail-active"
                      className="absolute inset-0 rounded-lg bg-primary/12 ring-1 ring-primary/30"
                      transition={{ type: 'spring', stiffness: 520, damping: 42 }}
                    />
                  )}
                  <Icon className="relative size-4 shrink-0" />
                  <span className="relative hidden truncate md:inline">{label}</span>
                </button>
              )
            })}
          </div>
        ))}
      </nav>

      <div className="mt-auto flex flex-col gap-0.5">
        <ConnectionStatus />

        <button
          type="button"
          role="switch"
          aria-checked={devMode}
          aria-label="Developer mode"
          onClick={() => onToggleDevMode(!devMode)}
          className={cn(rowButton, devMode && 'bg-accent-2/15 text-accent-2 hover:bg-accent-2/20 hover:text-accent-2')}
        >
          <Code2 className="size-4 shrink-0" />
          <span className="hidden truncate md:inline">Developer mode{devMode ? ': on' : ''}</span>
        </button>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <button type="button" aria-label="Theme" className={rowButton}>
              <SunMoon className="size-4 shrink-0" />
              <span className="hidden truncate md:inline">Theme</span>
            </button>
          </DropdownMenuTrigger>
          <DropdownMenuContent side="right" align="end" className="w-36">
            <DropdownMenuLabel className="text-xs text-muted-foreground">Theme</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as ThemeChoice)}>
              <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <button type="button" aria-label="Sign out" onClick={onSignOut} className={rowButton}>
          <LogOut className="size-4 shrink-0" />
          <span className="hidden truncate md:inline">Sign out</span>
        </button>
      </div>
    </aside>
  )
}
