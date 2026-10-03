import { useState } from 'react'
import { Database, Loader2, Plus, RefreshCw, Trash2 } from 'lucide-react'
import { toast } from 'sonner'

import { api, ApiError } from '@/api/client'
import type { Connection, ConnectionCheck } from '@/api/types'
import { Alert, AlertDescription } from '@/components/ui/alert'
import { Badge } from '@/components/ui/badge'
import { Button } from '@/components/ui/button'
import { Select, SelectContent, SelectItem, SelectTrigger, SelectValue } from '@/components/ui/select'
import { Skeleton } from '@/components/ui/skeleton'
import { AddDatabaseDialog } from '@/features/library/AddDatabaseDialog'
import { ConfirmDeleteDialog } from '@/features/library/FolderDialogs'
import { useConnections } from '@/features/library/useConnections'

const INTRO = 'Connect a Postgres database with a read-only login. StateSync only ever reads it.'
const NOT_SET_UP =
  'Connecting your own databases is not set up on this server. Run scripts/make_connection_key.py and restart the stack.'

const READ_ONLY_LOGIN = `CREATE ROLE statesync_reader LOGIN PASSWORD 'choose-a-password';
GRANT CONNECT ON DATABASE your_db TO statesync_reader;
GRANT USAGE ON SCHEMA your_schema TO statesync_reader;
GRANT SELECT ON ALL TABLES IN SCHEMA your_schema TO statesync_reader;`

/** What the last create or test told us about a connection; the server does not store it. */
interface Known {
  schemas: string[]
  warnings: string[]
}

function StatusBadge({ status }: { status: Connection['status'] }) {
  if (status === 'failed') return <Badge variant="destructive">Failed</Badge>
  return (
    <Badge variant="outline" className="border-success/40 font-normal text-success">
      Ready
    </Badge>
  )
}

interface ConnectionRowProps {
  connection: Connection
  known: Known | undefined
  testing: boolean
  onTest: (connection: Connection) => void
  onDelete: (connection: Connection) => void
  onSchemaChosen: (connection: Connection) => void
}

function ConnectionRow({ connection, known, testing, onTest, onDelete, onSchemaChosen }: ConnectionRowProps) {
  const [choosing, setChoosing] = useState(false)
  const [schemaError, setSchemaError] = useState<string | null>(null)
  const used = connection.workspace_ids.length

  const chooseSchema = async (schema: string) => {
    setChoosing(true)
    setSchemaError(null)
    try {
      await api.setConnectionSchema(connection.connection_id, schema)
      onSchemaChosen(connection)
    } catch (err) {
      setSchemaError(err instanceof ApiError ? err.message : 'The schema could not be saved.')
    } finally {
      setChoosing(false)
    }
  }

  return (
    <li
      data-testid="connection-item"
      data-status={connection.status}
      className="flex items-start gap-3 rounded-xl border bg-card p-4"
    >
      <div className="mt-0.5 grid size-9 shrink-0 place-items-center rounded-lg bg-muted text-muted-foreground">
        <Database className="size-4" />
      </div>
      <div className="min-w-0 flex-1 space-y-1.5">
        <div className="flex flex-wrap items-center gap-2">
          <h3 className="truncate font-medium text-foreground">{connection.name}</h3>
          <StatusBadge status={connection.status} />
        </div>
        <p className="truncate font-mono text-xs text-muted-foreground">
          {connection.username}@{connection.host}:{connection.port}/{connection.dbname}
        </p>
        {connection.check_detail && (
          <p className={connection.status === 'failed' ? 'text-sm text-destructive' : 'text-xs text-muted-foreground'}>
            {connection.check_detail}
          </p>
        )}
        {/* Warnings from the last check are stored as check_detail when the status is ok. */}
        <div className="flex flex-wrap items-center gap-2 text-sm">
          {connection.schema_name ? (
            <span className="text-muted-foreground">
              Schema <span className="font-medium text-foreground">{connection.schema_name}</span>
            </span>
          ) : known && known.schemas.length > 0 ? (
            <Select value="" onValueChange={(v) => void chooseSchema(v)} disabled={choosing}>
              <SelectTrigger aria-label={`Schema for ${connection.name}`} size="sm" className="min-w-44">
                <SelectValue placeholder="Choose a schema" />
              </SelectTrigger>
              <SelectContent>
                {known.schemas.map((schema) => (
                  <SelectItem key={schema} value={schema}>
                    {schema}
                  </SelectItem>
                ))}
              </SelectContent>
            </Select>
          ) : (
            <span className="text-muted-foreground">Choose a schema: use Test again to list the schemas.</span>
          )}
          {used > 0 && (
            <span className="text-muted-foreground">
              Used by {used} {used === 1 ? 'workspace' : 'workspaces'}
            </span>
          )}
        </div>
        {schemaError && (
          <p role="alert" className="text-sm text-destructive">
            {schemaError}
          </p>
        )}
      </div>
      <div className="flex shrink-0 items-center gap-1">
        <Button variant="outline" size="sm" onClick={() => onTest(connection)} disabled={testing} className="gap-2">
          {testing ? <Loader2 className="size-3.5 animate-spin" aria-hidden /> : <RefreshCw className="size-3.5" aria-hidden />}
          Test again
        </Button>
        <Button
          variant="ghost"
          size="icon"
          aria-label={`Delete connection ${connection.name}`}
          onClick={() => onDelete(connection)}
          className="text-muted-foreground hover:text-destructive"
        >
          <Trash2 className="size-4" />
        </Button>
      </div>
    </li>
  )
}

/** The Library's Databases tab (web/DESIGN.md §6b): connect and manage your own Postgres databases. */
export function DatabasesTab() {
  const { connections, available, isLoading, isError, error, refetch, refresh } = useConnections()
  const [addOpen, setAddOpen] = useState(false)
  const [deleting, setDeleting] = useState<Connection | null>(null)
  const [testingId, setTestingId] = useState<string | null>(null)
  const [known, setKnown] = useState<Record<string, Known>>({})

  const remember = (check: ConnectionCheck) =>
    setKnown((prev) => ({
      ...prev,
      [check.connection.connection_id]: { schemas: check.schemas, warnings: check.warnings },
    }))

  const test = async (connection: Connection) => {
    setTestingId(connection.connection_id)
    try {
      remember(await api.testConnection(connection.connection_id))
      refresh()
    } catch (err) {
      toast.error(err instanceof ApiError ? err.message : 'The connection could not be tested.')
    } finally {
      setTestingId(null)
    }
  }

  const addButton = available && (
    <Button onClick={() => setAddOpen(true)} className="gap-2">
      <Plus className="size-4" />
      Add database
    </Button>
  )

  return (
    <div className="space-y-4">
      {isLoading ? (
        <div className="space-y-3">
          <Skeleton className="h-6 w-96 max-w-full" />
          <Skeleton className="h-24 rounded-xl" />
        </div>
      ) : isError ? (
        <div className="flex flex-col items-start gap-3">
          <p className="text-sm text-muted-foreground">
            {error instanceof ApiError ? error.message : 'Your databases could not be loaded.'}
          </p>
          <Button variant="outline" onClick={() => void refetch()}>
            Retry
          </Button>
        </div>
      ) : (
        <>
          {!available && (
            <Alert>
              <AlertDescription>{NOT_SET_UP}</AlertDescription>
            </Alert>
          )}
          {connections.length === 0 ? (
            available && (
              <div className="flex h-72 flex-col items-center justify-center rounded-xl border border-dashed p-8 text-center">
                <div className="mb-4 grid size-12 place-items-center rounded-xl bg-muted/60 text-muted-foreground">
                  <Database className="size-6" />
                </div>
                <h2 className="mb-1.5 text-base font-semibold text-foreground">No databases yet</h2>
                <p className="mb-4 max-w-md text-sm text-muted-foreground">{INTRO}</p>
                {addButton}
              </div>
            )
          ) : (
            <>
              <div className="flex flex-wrap items-center justify-between gap-3">
                <p className="text-sm text-muted-foreground">{INTRO}</p>
                {addButton}
              </div>
              <ul className="space-y-3">
                {connections.map((c) => (
                  <ConnectionRow
                    key={c.connection_id}
                    connection={c}
                    known={known[c.connection_id]}
                    testing={testingId === c.connection_id}
                    onTest={(conn) => void test(conn)}
                    onDelete={setDeleting}
                    onSchemaChosen={refresh}
                  />
                ))}
              </ul>
            </>
          )}
          <details className="rounded-xl border bg-card p-4 text-sm">
            <summary className="cursor-pointer font-medium text-foreground">How to create a read-only login</summary>
            <p className="mt-3 text-muted-foreground">
              Run this as a database administrator, then connect with the new login. Use your own names and password.
            </p>
            <pre className="mt-3 overflow-x-auto rounded-lg bg-muted p-3 font-mono text-xs text-foreground">
              {READ_ONLY_LOGIN}
            </pre>
          </details>
        </>
      )}

      {addOpen && (
        <AddDatabaseDialog
          onOpenChange={setAddOpen}
          onConnected={(check) => {
            remember(check)
            refresh()
            setAddOpen(false)
            toast.success('Connected')
          }}
        />
      )}
      {deleting && (
        <ConfirmDeleteDialog
          open
          onOpenChange={(open) => !open && setDeleting(null)}
          title="Delete connection"
          onConfirm={async () => {
            await api.deleteConnection(deleting.connection_id)
            refresh()
            toast.success('Connection deleted')
          }}
        >
          <p>
            Delete <span className="font-semibold">{deleting.name}</span>? StateSync forgets the login. Your database
            is not changed.
          </p>
        </ConfirmDeleteDialog>
      )}
    </div>
  )
}
