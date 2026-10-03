import { useState } from 'react'

import { api, ApiError } from '@/api/client'
import type { ConnectionCheck, SslMode } from '@/api/types'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Button } from '@/components/ui/button'
import { Dialog, DialogContent, DialogFooter, DialogHeader, DialogTitle } from '@/components/ui/dialog'
import { Input } from '@/components/ui/input'
import { Label } from '@/components/ui/label'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'

const SSL_MODES: SslMode[] = ['disable', 'prefer', 'require', 'verify-ca', 'verify-full']

interface AddDatabaseDialogProps {
  onOpenChange: (open: boolean) => void
  onConnected: (check: ConnectionCheck) => void
}

/**
 * Connect a Postgres database (web/DESIGN.md §6b). Mount it only while it is
 * open. The password lives in this component's state alone: it goes out once in
 * the create request (called directly, not through a query or mutation cache),
 * and it is cleared when the dialog closes or succeeds. The server never returns it.
 */
export function AddDatabaseDialog({ onOpenChange, onConnected }: AddDatabaseDialogProps) {
  const [name, setName] = useState('')
  const [host, setHost] = useState('')
  const [port, setPort] = useState('5432')
  const [dbname, setDbname] = useState('')
  const [username, setUsername] = useState('')
  const [password, setPassword] = useState('')
  const [sslmode, setSslmode] = useState<SslMode>('prefer')
  const [busy, setBusy] = useState(false)
  const [error, setError] = useState<string | null>(null)

  const close = () => {
    setPassword('')
    onOpenChange(false)
  }

  const portNumber = Number(port)
  const portValid = /^\d+$/.test(port.trim()) && portNumber >= 1 && portNumber <= 65535
  // The server requires a password (a login without one cannot be stored).
  const ready = Boolean(name.trim() && host.trim() && dbname.trim() && username.trim() && password && portValid)

  const submit = async () => {
    if (!ready || busy) return
    setBusy(true)
    setError(null)
    try {
      const check = await api.createConnection({
        name: name.trim(),
        host: host.trim(),
        port: portNumber,
        dbname: dbname.trim(),
        username: username.trim(),
        password,
        sslmode,
      })
      setPassword('')
      onConnected(check)
    } catch (err) {
      setError(err instanceof ApiError ? err.message : 'The database could not be connected.')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Dialog open onOpenChange={(next) => !next && !busy && close()}>
      <DialogContent className="sm:max-w-md">
        <DialogHeader>
          <DialogTitle>Add database</DialogTitle>
        </DialogHeader>
        <form
          onSubmit={(e) => {
            e.preventDefault()
            void submit()
          }}
          className="space-y-4 py-2"
        >
          {error && (
            <Alert variant="destructive">
              <AlertDescription>{error}</AlertDescription>
            </Alert>
          )}
          <div className="space-y-1.5">
            <Label htmlFor="db-name">Name</Label>
            <Input id="db-name" value={name} onChange={(e) => setName(e.target.value)} placeholder="e.g. Shop replica" autoFocus />
          </div>
          <div className="grid grid-cols-[1fr_6rem] gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="db-host">Host</Label>
              <Input
                id="db-host"
                value={host}
                onChange={(e) => setHost(e.target.value)}
                aria-describedby="db-host-hint"
                autoComplete="off"
                spellCheck={false}
              />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="db-port">Port</Label>
              <Input
                id="db-port"
                inputMode="numeric"
                value={port}
                onChange={(e) => setPort(e.target.value)}
                aria-invalid={!portValid}
                autoComplete="off"
              />
            </div>
          </div>
          <p id="db-host-hint" className="-mt-2 text-xs text-muted-foreground">
            For a database on this computer, use host.docker.internal.
          </p>
          <div className="space-y-1.5">
            <Label htmlFor="db-dbname">Database</Label>
            <Input id="db-dbname" value={dbname} onChange={(e) => setDbname(e.target.value)} autoComplete="off" spellCheck={false} />
          </div>
          <div className="grid grid-cols-2 gap-3">
            <div className="space-y-1.5">
              <Label htmlFor="db-user">User</Label>
              <Input id="db-user" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="off" spellCheck={false} />
            </div>
            <div className="space-y-1.5">
              <Label htmlFor="db-password">Password</Label>
              <Input
                id="db-password"
                type="password"
                autoComplete="new-password"
                value={password}
                onChange={(e) => setPassword(e.target.value)}
              />
            </div>
          </div>
          <div className="space-y-1.5">
            <Label htmlFor="db-ssl">SSL</Label>
            <Select value={sslmode} onValueChange={(v) => setSslmode(v as SslMode)}>
              <SelectTrigger id="db-ssl" aria-label="SSL" className="w-full">
                <SelectValue />
              </SelectTrigger>
              <SelectContent>
                {SSL_MODES.map((mode) => (
                  <SelectItem key={mode} value={mode}>
                    {mode}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          </div>
          <DialogFooter className="pt-2">
            <Button type="button" variant="outline" onClick={close} disabled={busy}>
              Cancel
            </Button>
            <Button type="submit" disabled={!ready || busy}>
              {busy ? 'Checking…' : 'Connect'}
            </Button>
          </DialogFooter>
        </form>
      </DialogContent>
    </Dialog>
  )
}
