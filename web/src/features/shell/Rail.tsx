import { Boxes, Code2, LayoutDashboard, Library, LogOut, MessageSquare, SunMoon, type LucideIcon } from 'lucide-react'
import { motion } from 'motion/react'
import type { ReactNode } from 'react'

import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuLabel,
  DropdownMenuRadioGroup,
  DropdownMenuRadioItem,
  DropdownMenuTrigger,
} from '@/components/ui/dropdown-menu'
import { Tooltip, TooltipContent, TooltipTrigger } from '@/components/ui/tooltip'
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

const NAV: { route: AppRoute; label: string; icon: LucideIcon }[] = [
  { route: 'dashboards', label: 'Dashboards', icon: LayoutDashboard },
  { route: 'workspaces', label: 'Workspaces', icon: Boxes },
  { route: 'library', label: 'Library', icon: Library },
  { route: 'ask', label: 'Ask', icon: MessageSquare },
]

const iconButton =
  'relative grid size-10 place-items-center rounded-xl text-muted-foreground outline-none transition-colors ' +
  'hover:bg-accent hover:text-foreground focus-visible:ring-2 focus-visible:ring-ring'

function RailTooltip({ label, children }: { label: string; children: ReactNode }) {
  return (
    <Tooltip>
      <TooltipTrigger asChild>{children}</TooltipTrigger>
      <TooltipContent side="right">{label}</TooltipContent>
    </Tooltip>
  )
}

/** The slim navigation rail (web/DESIGN.md §4). */
export function Rail({ route, onNavigate, devMode, onToggleDevMode, onSignOut }: RailProps) {
  const { theme, setTheme } = useTheme()

  return (
    <aside className="flex w-16 shrink-0 flex-col items-center border-r bg-sidebar py-3">
      <RailTooltip label="StateSync">
        <button
          type="button"
          aria-label="StateSync home"
          onClick={() => onNavigate('dashboards')}
          className="grid size-10 place-items-center rounded-xl outline-none transition-transform hover:scale-105 focus-visible:ring-2 focus-visible:ring-ring"
        >
          <BrandMark className="size-8" />
        </button>
      </RailTooltip>

      <nav aria-label="Main" className="mt-6 flex flex-col gap-2">
        {NAV.map(({ route: target, label, icon: Icon }) => {
          const active = route === target
          return (
            <RailTooltip key={target} label={label}>
              <button
                type="button"
                aria-label={label}
                aria-current={active ? 'page' : undefined}
                onClick={() => onNavigate(target)}
                className={cn(iconButton, active && 'text-primary hover:bg-transparent hover:text-primary')}
              >
                {active && (
                  <motion.span
                    layoutId="rail-active"
                    className="absolute inset-0 rounded-xl bg-primary/12 ring-1 ring-primary/30"
                    transition={{ type: 'spring', stiffness: 520, damping: 42 }}
                  />
                )}
                <Icon className="relative size-5" />
              </button>
            </RailTooltip>
          )
        })}
      </nav>

      <div className="mt-auto flex flex-col items-center gap-1.5">
        <ConnectionStatus />

        <RailTooltip label={devMode ? 'Developer mode: on' : 'Developer mode: off'}>
          <button
            type="button"
            role="switch"
            aria-checked={devMode}
            aria-label="Developer mode"
            onClick={() => onToggleDevMode(!devMode)}
            className={cn(iconButton, devMode && 'bg-accent-2/15 text-accent-2 hover:bg-accent-2/20 hover:text-accent-2')}
          >
            <Code2 className="size-5" />
          </button>
        </RailTooltip>

        <DropdownMenu>
          <RailTooltip label="Theme">
            <DropdownMenuTrigger asChild>
              <button type="button" aria-label="Theme" className={iconButton}>
                <SunMoon className="size-5" />
              </button>
            </DropdownMenuTrigger>
          </RailTooltip>
          <DropdownMenuContent side="right" align="end" className="w-36">
            <DropdownMenuLabel className="text-xs text-muted-foreground">Theme</DropdownMenuLabel>
            <DropdownMenuRadioGroup value={theme} onValueChange={(value) => setTheme(value as ThemeChoice)}>
              <DropdownMenuRadioItem value="light">Light</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="dark">Dark</DropdownMenuRadioItem>
              <DropdownMenuRadioItem value="system">System</DropdownMenuRadioItem>
            </DropdownMenuRadioGroup>
          </DropdownMenuContent>
        </DropdownMenu>

        <RailTooltip label="Sign out">
          <button type="button" aria-label="Sign out" onClick={onSignOut} className={iconButton}>
            <LogOut className="size-5" />
          </button>
        </RailTooltip>
      </div>
    </aside>
  )
}
