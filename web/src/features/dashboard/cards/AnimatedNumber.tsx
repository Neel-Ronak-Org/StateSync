import { useEffect, useRef, useState, type HTMLAttributes } from 'react'
import { animate, useReducedMotion } from 'motion/react'

interface AnimatedNumberProps extends Omit<HTMLAttributes<HTMLSpanElement>, 'children'> {
  value: number
  format: (value: number) => string
}

/**
 * A number that counts from its previous value to the new one (web/DESIGN.md §3).
 * The first render shows the value as is; reduced motion shows every change at once.
 * Whole numbers stay whole while counting.
 */
export function AnimatedNumber({ value, format, ...rest }: AnimatedNumberProps) {
  const [shown, setShown] = useState(value)
  const previous = useRef(value)
  const reduce = useReducedMotion()

  useEffect(() => {
    const from = previous.current
    previous.current = value
    if (reduce || from === value) {
      setShown(value)
      return
    }
    const whole = Number.isInteger(from) && Number.isInteger(value)
    const controls = animate(from, value, {
      duration: 0.6,
      ease: 'easeOut',
      onUpdate: (v) => setShown(whole ? Math.round(v) : v),
      onComplete: () => setShown(value),
    })
    return () => controls.stop()
  }, [value, reduce])

  return <span {...rest}>{format(shown)}</span>
}
