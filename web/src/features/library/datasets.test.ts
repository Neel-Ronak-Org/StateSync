import { describe, expect, it } from 'vitest'

import type { Connection } from '@/api/types'
import { datasetLabel, isConnectionId } from '@/features/library/datasets'

function connection(id: string, name: string): Connection {
  return {
    connection_id: id,
    name,
    host: 'db.example.com',
    port: 5432,
    dbname: 'shop',
    username: 'reader',
    sslmode: 'prefer',
    schema_name: 'public',
    status: 'ok',
    check_detail: null,
    checked_at: null,
    created_at: '2026-09-01T09:00:00Z',
    workspace_ids: [],
  }
}

const ID = 'cn_0123456789abcdef'

describe('datasetLabel', () => {
  it('shows the connection name for a connection id', () => {
    expect(datasetLabel(ID, [connection('cn_ffffffffffffffff', 'Other'), connection(ID, 'Shop replica')])).toBe(
      'Shop replica',
    )
  })

  it('leaves a sample dataset or schema as it is', () => {
    const list = [connection(ID, 'Shop replica')]
    expect(datasetLabel('olist', list)).toBe('olist')
    expect(datasetLabel('public', list)).toBe('public')
    expect(datasetLabel('live', undefined)).toBe('live')
  })

  it('never prints a raw id when the connection is unknown or not loaded', () => {
    expect(datasetLabel(ID, [])).toBe('Your database')
    expect(datasetLabel(ID, undefined)).toBe('Your database')
  })

  it('only treats cn_ plus 16 lowercase hex characters as a connection id', () => {
    const list = [connection(ID, 'Shop replica')]
    for (const near of ['cn_0123456789abcde', 'cn_0123456789abcdefa', 'CN_0123456789abcdef', 'cn_0123456789abcdeg', ' cn_0123456789abcdef']) {
      expect(isConnectionId(near)).toBe(false)
      expect(datasetLabel(near, list)).toBe(near)
    }
    expect(isConnectionId(ID)).toBe(true)
  })
})
