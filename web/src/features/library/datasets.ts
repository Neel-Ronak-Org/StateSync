import type { Connection } from '@/api/types'

const CONNECTION_ID = /^cn_[0-9a-f]{16}$/

/** True for the id of a connected database, which a workspace uses as its dataset. */
export function isConnectionId(value: string | null | undefined): value is string {
  return typeof value === 'string' && CONNECTION_ID.test(value)
}

/**
 * The words to show for a dataset: the connection's name when the dataset is a
 * connection id, else the dataset as it is. An id we cannot match (the list is
 * still loading, or the connection was deleted) reads "Your database" rather
 * than a raw id.
 */
export function datasetLabel(targetSchema: string, connections: readonly Connection[] | undefined): string {
  if (!isConnectionId(targetSchema)) return targetSchema
  return connections?.find((c) => c.connection_id === targetSchema)?.name ?? 'Your database'
}
