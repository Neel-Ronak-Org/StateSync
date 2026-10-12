import { useQuery } from '@tanstack/react-query'

import { api, ApiError } from '@/api/client'
import { Skeleton } from '@/components/ui/skeleton'
import { SchemaDiagram } from '@/features/inspect/SchemaDiagram'

/** The workspace's database as a diagram: every table, its columns and the keys that link them. */
export function SchemaTab({ workspaceId }: { workspaceId: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ['schema-graph', workspaceId],
    queryFn: () => api.workspaceSchemaGraph(workspaceId),
    staleTime: 5 * 60 * 1000,
  })

  if (isLoading) return <Skeleton className="h-72 w-full" />
  if (!data) {
    return (
      <p className="text-sm text-muted-foreground">
        {error instanceof ApiError ? error.message : "The database's shape could not be loaded."}
      </p>
    )
  }
  const columns = data.tables.reduce((sum, t) => sum + t.columns.length, 0)
  return (
    <div className="space-y-3">
      <p className="text-sm text-muted-foreground">
        {data.tables.length} {data.tables.length === 1 ? 'table' : 'tables'}, {columns} columns and {data.edges.length}{' '}
        {data.edges.length === 1 ? 'link' : 'links'} between them. Read from the database's own catalog; no row of data is fetched. A key
        icon marks a column that points to another table.
      </p>
      <SchemaDiagram graph={data} columns={4} />
    </div>
  )
}
