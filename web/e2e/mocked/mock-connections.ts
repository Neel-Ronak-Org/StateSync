/**
 * The /library/connections part of the mocked orchestrator (docs/V3_PLAN.md Phase 3c
 * slice 6a): a user's own read-only Postgres databases. Mirrors the server's rules:
 * no password field in any response, 422 with a `detail` sentence for every refusal
 * (a login that can write, the platform's own database host, a bad password, a
 * duplicate name, a schema that is not readable, a connection that workspaces use),
 * 409 at the limit, and a create that picks the schema itself when the login can
 * read exactly one. A connection with a schema is a dataset: its id is what
 * POST /workspaces takes as target_schema.
 */

import type { Route } from '@playwright/test'

type Json = Record<string, unknown>
type Reply = (route: Route, status: number, body: unknown) => Promise<void>

export interface MockConnection {
  connection_id: string
  name: string
  host: string
  port: number
  dbname: string
  username: string
  sslmode: string
  schema_name: string | null
  status: 'ok' | 'failed'
  check_detail: string | null
  checked_at: string
  created_at: string
}

export const CONNECTION_LIMIT = 5

export class ConnectionsMock {
  readonly connections = new Map<string, MockConnection>()
  /** False mimics a server without an encryption key: the list says so and creation is refused. */
  available = true
  /** The schemas each database (by dbname) lets the login read. Unlisted databases have only `public`. */
  schemasByDb: Record<string, string[]> = {}
  /** Every POST /library/connections body, exactly as the browser sent it. */
  readonly creates: Json[] = []
  readonly schemaPuts: { id: string; schema_name: unknown }[] = []
  readonly tests: string[] = []
  readonly deletes: string[] = []
  /** When set for a connection id, its next test fails with this reason (the database went away). */
  failNextTest = new Map<string, string>()
  /** Warnings the next create or test reports (for example an unencrypted connection); cleared after use. */
  nextWarnings: string[] = []
  /** Set by MockApi: the workspaces that use a connection (target_schema === its id). */
  workspaceIdsFor: (connectionId: string) => string[] = () => []
  private seq = 0
  private clock = Date.parse('2026-09-28T09:00:00Z')

  private now(): string {
    this.clock += 1000
    return new Date(this.clock).toISOString()
  }

  /** A connection that already exists (seed for tests). */
  add(name: string, options: { schema?: string | null; dbname?: string; status?: 'ok' | 'failed'; detail?: string } = {}): MockConnection {
    this.seq += 1
    const c: MockConnection = {
      connection_id: `cn_${this.seq.toString(16).padStart(16, '0')}`,
      name,
      host: 'db.example.com',
      port: 5432,
      dbname: options.dbname ?? 'shop',
      username: 'statesync_reader',
      sslmode: 'prefer',
      schema_name: options.schema === undefined ? 'public' : options.schema,
      status: options.status ?? 'ok',
      check_detail: options.detail ?? null,
      checked_at: this.now(),
      created_at: this.now(),
    }
    this.connections.set(c.connection_id, c)
    return c
  }

  /** True when the id is a connection that can be a workspace's dataset (it has a schema). */
  hasDataset(id: string): boolean {
    return Boolean(this.connections.get(id)?.schema_name)
  }

  private view(c: MockConnection) {
    return { ...c, workspace_ids: this.workspaceIdsFor(c.connection_id) }
  }

  private schemasOf(c: MockConnection): string[] {
    return this.schemasByDb[c.dbname] ?? ['public']
  }

  private check(c: MockConnection) {
    const warnings = this.nextWarnings
    this.nextWarnings = []
    // As the server: an ok check stores its warnings as check_detail.
    if (c.status === 'ok') c.check_detail = warnings.join(' ') || null
    return { connection: this.view(c), schemas: this.schemasOf(c), warnings }
  }

  private duplicateName(name: string): boolean {
    return [...this.connections.values()].some((c) => c.name.toLowerCase() === name.toLowerCase())
  }

  async handle(route: Route, method: string, path: string, body: Json, reply: Reply): Promise<void> {
    const parts = path.split('/').filter(Boolean) // ['library', 'connections', id?, sub?]
    const [, , id, sub] = parts

    if (!id) {
      if (method === 'GET') {
        return reply(route, 200, { connections: [...this.connections.values()].map((c) => this.view(c)), available: this.available })
      }
      if (method === 'POST') return this.create(route, body, reply)
      return reply(route, 405, { detail: 'Method Not Allowed' })
    }

    const c = this.connections.get(id)
    if (!c) return reply(route, 404, { detail: 'No such connection.' })

    if (!sub && method === 'DELETE') {
      const used = this.workspaceIdsFor(id).length
      if (used > 0) {
        return reply(route, 422, { detail: `${used} ${used === 1 ? 'workspace uses' : 'workspaces use'} this connection. Delete ${used === 1 ? 'it' : 'them'} first.` })
      }
      this.deletes.push(id)
      this.connections.delete(id)
      return reply(route, 200, { deleted: 1 })
    }

    if (sub === 'schema' && method === 'PUT') {
      this.schemaPuts.push({ id, schema_name: body.schema_name })
      if (this.workspaceIdsFor(id).length > 0) {
        return reply(route, 422, { detail: 'Workspaces already use this connection, so its schema cannot change.' })
      }
      const schema = String(body.schema_name ?? '')
      if (!this.schemasOf(c).includes(schema)) {
        return reply(route, 422, { detail: `The schema ${schema} is not readable with this login.` })
      }
      c.schema_name = schema
      return reply(route, 200, { connection: this.view(c) })
    }

    if (sub === 'test' && method === 'POST') {
      this.tests.push(id)
      c.checked_at = this.now()
      const reason = this.failNextTest.get(id)
      if (reason) {
        this.failNextTest.delete(id)
        c.status = 'failed'
        c.check_detail = reason
      } else {
        c.status = 'ok'
        c.check_detail = null
      }
      return reply(route, 200, this.check(c))
    }

    return reply(route, 404, { detail: 'Not Found' })
  }

  private async create(route: Route, body: Json, reply: Reply): Promise<void> {
    this.creates.push(body)
    if (!this.available) return reply(route, 422, { detail: 'Connections are not set up on this server (no encryption key).' })
    if (this.connections.size >= CONNECTION_LIMIT) {
      return reply(route, 409, { detail: `You already have ${CONNECTION_LIMIT} connections; the limit is ${CONNECTION_LIMIT}.` })
    }
    const name = String(body.name ?? '').trim()
    const host = String(body.host ?? '').trim()
    const username = String(body.username ?? '')
    if (!name) return reply(route, 422, { detail: 'A connection needs a name.' })
    if (this.duplicateName(name)) return reply(route, 422, { detail: 'A connection with that name already exists.' })
    if (host === 'postgres') {
      return reply(route, 422, {
        detail: 'postgres is part of StateSync itself. For a database on this computer, use host.docker.internal.',
      })
    }
    if (!String(body.password ?? '')) return reply(route, 422, { detail: 'password must not be blank.' })
    if (String(body.password ?? '') === 'wrong-password') {
      return reply(route, 422, { detail: `Could not connect: password authentication failed for user "${username}".` })
    }
    if (username.includes('writer')) {
      return reply(route, 422, {
        detail: 'This login can change data (for example in shop.orders). Use a login that can only read.',
      })
    }
    const c = this.add(name, { dbname: String(body.dbname ?? ''), schema: null })
    c.host = host
    c.port = Number(body.port)
    c.username = username
    c.sslmode = String(body.sslmode ?? 'prefer')
    const schemas = this.schemasOf(c)
    if (schemas.length === 1) c.schema_name = schemas[0]
    return reply(route, 201, this.check(c))
  }
}
