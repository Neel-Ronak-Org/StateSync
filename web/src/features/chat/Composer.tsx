import { useEffect, useRef, useState, type KeyboardEvent } from 'react'
import { ArrowUp, Loader2 } from 'lucide-react'

import { Button } from '@/components/ui/button'
import { Textarea } from '@/components/ui/textarea'

interface ComposerProps {
  onSend: (question: string) => void
  isPending: boolean
}

export function Composer({ onSend, isPending }: ComposerProps) {
  const [value, setValue] = useState('')
  const textareaRef = useRef<HTMLTextAreaElement>(null)

  // Focus textarea when user presses '/' outside of inputs/textareas
  useEffect(() => {
    const handleGlobalKeyDown = (e: globalThis.KeyboardEvent) => {
      if (
        e.key === '/' &&
        document.activeElement?.tagName !== 'INPUT' &&
        document.activeElement?.tagName !== 'TEXTAREA'
      ) {
        e.preventDefault()
        textareaRef.current?.focus()
      }
    }
    window.addEventListener('keydown', handleGlobalKeyDown)
    return () => window.removeEventListener('keydown', handleGlobalKeyDown)
  }, [])

  const handleSend = () => {
    const trimmed = value.trim()
    if (!trimmed || isPending) return
    onSend(trimmed)
    setValue('')
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
    }
  }

  const handleKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSend()
    }
  }

  const handleChange = (val: string) => {
    setValue(val)
    if (textareaRef.current) {
      textareaRef.current.style.height = 'auto'
      textareaRef.current.style.height = `${Math.min(textareaRef.current.scrollHeight, 180)}px`
    }
  }

  return (
    <div className="relative flex items-end gap-2 rounded-xl border bg-background p-2 shadow-xs focus-within:border-ring focus-within:ring-2 focus-within:ring-ring/50 transition-all">
      <Textarea
        ref={textareaRef}
        aria-label="Ask a question"
        placeholder="Ask about your data…"
        value={value}
        onChange={(e) => handleChange(e.target.value)}
        onKeyDown={handleKeyDown}
        rows={1}
        className="min-h-9 max-h-44 resize-none border-0 bg-transparent dark:bg-transparent p-1.5 text-sm shadow-none focus-visible:ring-0 leading-snug"
      />
      <Button
        type="button"
        size="icon-sm"
        aria-label="Send"
        disabled={isPending || !value.trim()}
        onClick={handleSend}
        className="shrink-0 mb-0.5"
      >
        {isPending ? <Loader2 className="size-3.5 animate-spin" /> : <ArrowUp className="size-3.5" />}
      </Button>
    </div>
  )
}
