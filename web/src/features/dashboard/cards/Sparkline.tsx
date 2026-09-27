import { useId } from 'react'

import { cn } from '@/lib/utils'

interface SparklineProps {
  values: number[]
  className?: string
}

const W = 120
const H = 36
const PAD = 2

/** A small trend line with a fading fill, drawn from numbers only (web/DESIGN.md §9). */
export function Sparkline({ values, className }: SparklineProps) {
  const gradientId = `spark-${useId().replace(/[^a-zA-Z0-9_-]/g, '')}`
  const finite = values.filter((v) => Number.isFinite(v))
  if (finite.length < 2) return null
  const min = Math.min(...finite)
  const max = Math.max(...finite)
  const span = max - min || 1
  const step = W / (finite.length - 1)
  const points = finite.map((v, i) => [i * step, PAD + (H - 2 * PAD) * (1 - (v - min) / span)] as const)
  const line = points.map(([x, y], i) => `${i === 0 ? 'M' : 'L'}${x.toFixed(1)} ${y.toFixed(1)}`).join(' ')
  const area = `${line} L${W} ${H} L0 ${H} Z`
  const last = points[points.length - 1]

  return (
    <svg
      data-testid="sparkline"
      viewBox={`0 0 ${W} ${H}`}
      preserveAspectRatio="none"
      role="img"
      aria-label={`Trend over the last ${finite.length} points`}
      className={cn('overflow-visible', className)}
    >
      <defs>
        <linearGradient id={gradientId} x1="0" y1="0" x2="0" y2="1">
          <stop offset="0%" stopColor="currentColor" stopOpacity="0.35" />
          <stop offset="100%" stopColor="currentColor" stopOpacity="0" />
        </linearGradient>
      </defs>
      <path d={area} fill={`url(#${gradientId})`} />
      <path d={line} fill="none" stroke="currentColor" strokeWidth="2" strokeLinejoin="round" vectorEffect="non-scaling-stroke" />
      <circle cx={last[0]} cy={last[1]} r="2.5" fill="currentColor" />
    </svg>
  )
}
