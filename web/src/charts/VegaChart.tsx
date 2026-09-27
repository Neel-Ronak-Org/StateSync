/**
 * The only place a chart is drawn.
 *
 * Safety, in order:
 * 1. validateA7Chart runs first (validator 2 of 2; the server ran validator 1).
 *    Any violation withholds the chart. Nothing is repaired.
 * 2. Vega evaluates expressions with vega-interpreter (`ast: true` +
 *    `expr: expressionInterpreter`), never its default compiler, which builds
 *    functions with `Function(...)`. The CSP has no 'unsafe-eval', so the
 *    compiler path would throw -- the interpreter is what makes rendering work
 *    under that CSP rather than the CSP being loosened for it.
 * 3. No tooltips, and nothing from the spec or the rows is ever written as
 *    HTML. vega-embed installs vega-tooltip (an innerHTML writer) unless told
 *    `tooltip: false`; the theme's mark.tooltip:false alone does not stop a
 *    spec's explicit tooltip channel, which A7 does not forbid.
 *
 * Width: vega-embed's `width` OPTION is ignored for Vega-Lite specs (measured
 * earlier: 98px via the option vs the full frame via the spec), so the width
 * goes on a COPY of the spec, and the copy is validated again before use.
 */

import { useEffect, useRef, useState } from 'react'
import embed, { type Result } from 'vega-embed'
import { expressionInterpreter } from 'vega-interpreter'

import type { ChartSpec } from '@/api/types'
import { A7, validateA7Chart } from '@/charts/a7'
import { vegaConfig } from '@/charts/theme'
import { useTheme } from '@/components/theme-provider'
import { cn } from '@/lib/utils'

/** The live Vega view, for PNG export. Import this type instead of vega-embed. */
export type ChartView = Result['view']
type EmbeddedView = ChartView

interface VegaChartProps {
  spec: ChartSpec
  className?: string
  /** Height in px for the chart body (the spec's own height wins if it has one). */
  height?: number
  /** Receives the live Vega view (e.g. for PNG export) and null on teardown. */
  onView?: (view: EmbeddedView | null) => void
  /** Show the violation list when a chart is withheld (developer mode). */
  showViolations?: boolean
}

/**
 * Why no chart is shown. 'unsafe': the A7 check refused the spec, so Vega never
 * saw it. 'failed': the spec passed, but Vega could not draw it. The two read
 * differently, because blaming the safety check for a rendering bug is wrong.
 */
interface Problem {
  kind: 'unsafe' | 'failed'
  details: string[]
}

const MIN_FRAME_PX = 40
const FRAME_GUTTER_PX = 8

export function VegaChart({ spec, className, height, onView, showViolations = false }: VegaChartProps) {
  const frameRef = useRef<HTMLDivElement>(null)
  const hostRef = useRef<HTMLDivElement>(null)
  const { resolvedTheme } = useTheme()
  const [width, setWidth] = useState(0)
  const [problem, setProblem] = useState<Problem | null>(null)
  // Held in a ref so a parent passing an inline callback does not re-embed the
  // chart on every render.
  const onViewRef = useRef(onView)
  useEffect(() => {
    onViewRef.current = onView
  }, [onView])

  // Track the frame width; re-embedding on resize keeps the chart sharp.
  useEffect(() => {
    const frame = frameRef.current
    if (!frame) return
    let raf = 0
    const observer = new ResizeObserver((entries) => {
      const next = Math.floor(entries[0]?.contentRect.width ?? 0)
      cancelAnimationFrame(raf)
      raf = requestAnimationFrame(() => setWidth((prev) => (Math.abs(prev - next) > 2 ? next : prev)))
    })
    observer.observe(frame)
    return () => {
      cancelAnimationFrame(raf)
      observer.disconnect()
    }
  }, [])

  useEffect(() => {
    const host = hostRef.current
    if (!host) return

    const violations = validateA7Chart(spec)
    if (violations.length > 0) {
      console.error('[A7] chart withheld before render, not repaired:', violations)
      // A finalized view leaves its canvas in the host; a withheld chart must
      // leave nothing drawn, not an earlier chart merely hidden.
      host.replaceChildren()
      setProblem({ kind: 'unsafe', details: violations })
      onViewRef.current?.(null)
      return
    }
    if (width < MIN_FRAME_PX) return

    let draw: ChartSpec = spec
    const sized: ChartSpec = { ...spec }
    if (!('width' in spec)) {
      sized.width = Math.min(A7.MAX_WIDTH, Math.max(A7.MIN_DIM, width - FRAME_GUTTER_PX))
    }
    if (!('height' in spec) && height) {
      sized.height = Math.min(A7.MAX_HEIGHT, Math.max(A7.MIN_DIM, height))
    }
    if (validateA7Chart(sized).length === 0) draw = sized

    let cancelled = false
    let result: Result | null = null
    embed(host, draw as never, {
      renderer: 'canvas',
      actions: false,
      tooltip: false,
      config: vegaConfig() as never,
      ast: true,
      expr: expressionInterpreter,
    })
      .then((r) => {
        if (cancelled) {
          r.finalize()
          return
        }
        result = r
        setProblem(null)
        onViewRef.current?.(r.view)
      })
      .catch((err: unknown) => {
        if (cancelled) return
        console.error('[chart] render failed:', err)
        host.replaceChildren()
        setProblem({ kind: 'failed', details: [err instanceof Error ? err.message : String(err)] })
        onViewRef.current?.(null)
      })

    return () => {
      cancelled = true
      result?.finalize()
      onViewRef.current?.(null)
    }
    // resolvedTheme: a compiled view keeps its config, so a theme change re-embeds.
  }, [spec, width, height, resolvedTheme])

  return (
    <div ref={frameRef} className={cn('w-full', className)}>
      {problem ? (
        <div role="note" className="rounded-md border border-dashed px-3 py-2 text-xs text-muted-foreground">
          {problem.kind === 'unsafe'
            ? 'Chart withheld: it did not pass the chart safety check. The answer above is unaffected.'
            : 'The chart could not be drawn. The answer above is unaffected.'}
          {showViolations && (
            <ul className="mt-1 list-disc pl-4 font-mono">
              {problem.details.map((p) => (
                <li key={p}>{p}</li>
              ))}
            </ul>
          )}
        </div>
      ) : null}
      <div ref={hostRef} className={cn(problem && 'hidden')} data-testid="vega-chart" />
    </div>
  )
}
