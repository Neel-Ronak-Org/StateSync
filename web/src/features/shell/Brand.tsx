import { cn } from '@/lib/utils'

/** The mark: three rising bars on a cyan tile. Reads the same on navy and on white. */
export function BrandMark({ className }: { className?: string }) {
  return (
    <svg className={cn('size-5 shrink-0', className)} viewBox="0 0 32 32" fill="none" aria-hidden="true">
      <rect width="32" height="32" rx="9" fill="#5cc8e8" />
      <rect x="8" y="17" width="4" height="7" rx="1.5" fill="#0b1220" fillOpacity="0.55" />
      <rect x="14" y="12" width="4" height="12" rx="1.5" fill="#0b1220" fillOpacity="0.8" />
      <rect x="20" y="8" width="4" height="16" rx="1.5" fill="#0b1220" />
    </svg>
  )
}

export function Brand({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <BrandMark />
      <span className="text-sm font-semibold tracking-tight">StateSync</span>
    </div>
  )
}
