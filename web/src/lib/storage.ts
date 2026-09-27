/**
 * Browser storage that never throws.
 *
 * A browser with site data blocked (or a private window) throws on every
 * localStorage access; an uncaught throw during render white-screens the app.
 * Every read and write goes through here and degrades to an in-memory value,
 * so the app keeps working for the session even when nothing can persist.
 */

const memory = new Map<string, string>()

type Area = 'local' | 'session'

function area(which: Area): Storage | null {
  try {
    return which === 'local' ? window.localStorage : window.sessionStorage
  } catch {
    return null
  }
}

export function readStored(key: string, which: Area = 'local'): string | null {
  try {
    const value = area(which)?.getItem(key)
    if (value !== null && value !== undefined) return value
  } catch {
    /* fall through to memory */
  }
  return memory.get(`${which}:${key}`) ?? null
}

export function writeStored(key: string, value: string | null, which: Area = 'local'): void {
  const memKey = `${which}:${key}`
  if (value === null) memory.delete(memKey)
  else memory.set(memKey, value)
  try {
    const store = area(which)
    if (!store) return
    if (value === null) store.removeItem(key)
    else store.setItem(key, value)
  } catch {
    /* in-memory copy already updated */
  }
}

export function readJson<T>(key: string, fallback: T, which: Area = 'local'): T {
  const raw = readStored(key, which)
  if (raw === null) return fallback
  try {
    return JSON.parse(raw) as T
  } catch {
    return fallback
  }
}

export function writeJson(key: string, value: unknown, which: Area = 'local'): void {
  writeStored(key, JSON.stringify(value), which)
}

/** Keys in one place, so a rename cannot orphan a value. */
export const KEYS = {
  token: 'statesync.token',
  corpus: 'statesync.corpus',
  session: 'statesync.session',
  thread: 'statesync.thread',
  theme: 'statesync.theme',
  devMode: 'statesync.devMode',
} as const

/** The dashboard last opened on a dataset, so returning to it opens the same tab. */
export function dashboardKey(dataset: string): string {
  return `statesync.dashboard.${dataset}`
}
