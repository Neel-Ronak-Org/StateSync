import { useEffect, useRef, useState } from 'react'

/** The rendered size of an element, kept current with a ResizeObserver (rounded to whole pixels). */
export function useElementSize<T extends HTMLElement>() {
  const ref = useRef<T>(null)
  const [size, setSize] = useState({ width: 0, height: 0 })
  useEffect(() => {
    const el = ref.current
    if (!el) return
    let frame = 0
    const observer = new ResizeObserver((entries) => {
      const rect = entries[0]?.contentRect
      if (!rect) return
      cancelAnimationFrame(frame)
      frame = requestAnimationFrame(() =>
        setSize((prev) => {
          const next = { width: Math.round(rect.width), height: Math.round(rect.height) }
          return prev.width === next.width && prev.height === next.height ? prev : next
        }),
      )
    })
    observer.observe(el)
    return () => {
      cancelAnimationFrame(frame)
      observer.disconnect()
    }
  }, [])
  return { ref, ...size }
}
