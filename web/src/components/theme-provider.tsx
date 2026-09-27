/**
 * Light / dark / system theme.
 *
 * Written here instead of using next-themes because that package injects an
 * inline <script> to avoid a flash of the wrong theme, and the CSP
 * (web/nginx.conf) forbids inline scripts. This applies the `dark` class on
 * <html> from an effect and follows the OS setting while in "system".
 */

import { createContext, useContext, useEffect, useLayoutEffect, useMemo, useState, type ReactNode } from 'react'

import { KEYS, readStored, writeStored } from '@/lib/storage'

export type ThemeChoice = 'light' | 'dark' | 'system'
export type ResolvedTheme = 'light' | 'dark'

interface ThemeContextValue {
  theme: ThemeChoice
  resolvedTheme: ResolvedTheme
  setTheme: (theme: ThemeChoice) => void
}

const ThemeContext = createContext<ThemeContextValue | null>(null)

function systemTheme(): ResolvedTheme {
  try {
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light'
  } catch {
    return 'light'
  }
}

function storedChoice(): ThemeChoice {
  const value = readStored(KEYS.theme)
  return value === 'light' || value === 'dark' || value === 'system' ? value : 'dark'
}

export function ThemeProvider({ children }: { children: ReactNode }) {
  const [theme, setThemeState] = useState<ThemeChoice>(storedChoice)
  const [system, setSystem] = useState<ResolvedTheme>(systemTheme)

  useEffect(() => {
    let media: MediaQueryList | null = null
    try {
      media = window.matchMedia('(prefers-color-scheme: dark)')
    } catch {
      return
    }
    const onChange = () => setSystem(media?.matches ? 'dark' : 'light')
    media.addEventListener('change', onChange)
    return () => media?.removeEventListener('change', onChange)
  }, [])

  const resolvedTheme: ResolvedTheme = theme === 'system' ? system : theme

  // A LAYOUT effect on purpose: React runs children's effects before their
  // parents', so a chart re-theming in its own effect would read the CSS
  // variables before a plain effect here had switched the class, and draw a
  // light chart on a dark page. Layout effects all run before passive ones.
  useLayoutEffect(() => {
    const root = document.documentElement
    root.classList.toggle('dark', resolvedTheme === 'dark')
    root.style.colorScheme = resolvedTheme
  }, [resolvedTheme])

  const value = useMemo<ThemeContextValue>(
    () => ({
      theme,
      resolvedTheme,
      setTheme: (next) => {
        writeStored(KEYS.theme, next)
        setThemeState(next)
      },
    }),
    [theme, resolvedTheme],
  )

  return <ThemeContext.Provider value={value}>{children}</ThemeContext.Provider>
}

// eslint-disable-next-line react-refresh/only-export-components
export function useTheme(): ThemeContextValue {
  const ctx = useContext(ThemeContext)
  if (!ctx) throw new Error('useTheme must be used inside <ThemeProvider>')
  return ctx
}
