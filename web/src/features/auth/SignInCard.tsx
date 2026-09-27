import { useState, type FormEvent } from 'react'
import { Loader2 } from 'lucide-react'

import { api, ApiError } from '@/api/client'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Card, CardContent, CardDescription, CardHeader } from '@/components/ui/card'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Brand } from '@/features/shell/Brand'
import { clearToken, setToken } from '@/lib/auth'

interface SignInCardProps {
  onSuccess: () => void
  sessionExpiredMessage?: string | null
  onAttemptStart?: () => void
  onAttemptEnd?: () => void
}

export function SignInCard({
  onSuccess,
  sessionExpiredMessage,
  onAttemptStart,
  onAttemptEnd,
}: SignInCardProps) {
  const [tokenInput, setTokenInput] = useState('')
  const [errorMessage, setErrorMessage] = useState<string | null>(null)
  const [isSubmitting, setIsSubmitting] = useState(false)

  const handleSubmit = async (e: FormEvent) => {
    e.preventDefault()
    const trimmed = tokenInput.trim()
    if (!trimmed) return

    setIsSubmitting(true)
    setErrorMessage(null)
    onAttemptStart?.()

    // Store token so apiFetch includes it in Authorization header.
    setToken(trimmed)

    try {
      await api.sessions()
      onSuccess()
    } catch (err) {
      clearToken()
      if (err instanceof ApiError && err.status === 401) {
        setErrorMessage('That token was not accepted. Check it and try again.')
      } else if (err instanceof ApiError) {
        setErrorMessage(err.message)
      } else {
        setErrorMessage('Failed to sign in. Please try again.')
      }
    } finally {
      setIsSubmitting(false)
      onAttemptEnd?.()
    }
  }

  const alertMessage = errorMessage || sessionExpiredMessage

  return (
    <div className="flex min-h-screen w-full items-center justify-center p-4 bg-background">
      <Card className="w-full max-w-sm border shadow-xs">
        <CardHeader className="space-y-2 pb-4">
          <Brand className="mb-2" />
          <h1 className="text-lg font-semibold tracking-tight">Sign in to StateSync</h1>
          <CardDescription className="text-xs text-muted-foreground">
            Paste the API token your administrator gave you.
          </CardDescription>
        </CardHeader>
        <CardContent>
          <form onSubmit={handleSubmit} className="space-y-4">
            {alertMessage && (
              <Alert variant="destructive">
                <AlertDescription className="text-xs">{alertMessage}</AlertDescription>
              </Alert>
            )}

            <div className="space-y-1.5">
              <Label htmlFor="api-token" className="text-xs font-medium">
                API token
              </Label>
              <Input
                id="api-token"
                type="password"
                autoComplete="off"
                placeholder="Token"
                value={tokenInput}
                onChange={(e) => setTokenInput(e.target.value)}
                disabled={isSubmitting}
                required
                className="h-8 text-sm"
              />
            </div>

            <Button type="submit" className="w-full h-8 text-sm" disabled={isSubmitting || !tokenInput.trim()}>
              {isSubmitting ? (
                <>
                  <Loader2 className="size-3.5 animate-spin" />
                  <span>Signing in…</span>
                </>
              ) : (
                'Sign in'
              )}
            </Button>
          </form>
        </CardContent>
      </Card>
    </div>
  )
}
