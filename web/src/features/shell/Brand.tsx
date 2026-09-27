import { cn } from '@/lib/utils'

export function Brand({ className }: { className?: string }) {
  return (
    <div className={cn('flex items-center gap-2', className)}>
      <svg
        className="size-5 shrink-0"
        viewBox="0 0 32 32"
        fill="none"
        xmlns="http://www.w3.org/2000/svg"
        aria-hidden="true"
      >
        <rect width="32" height="32" rx="8" fill="#18181b" />
        <rect x="8" y="17" width="4" height="7" rx="1.5" fill="#818cf8" />
        <rect x="14" y="12" width="4" height="12" rx="1.5" fill="#fafafa" />
        <rect x="20" y="8" width="4" height="16" rx="1.5" fill="#fafafa" />
      </svg>
      <span className="text-sm font-semibold tracking-tight">StateSync</span>
    </div>
  )
}
