import type { ChartSpec, Citation, Route, Row, StateTrace } from '@/api/types'

export interface ChatTurn {
  id: string
  question: string
  answer: string
  chart: ChartSpec | null
  rows: Row[] | null
  columns: string[]
  sql: string | null
  route: Route | null
  turnIndex: number
  sqlRowsCount: number
  dataset: string
  citations: Citation[]
  latencyMs: number
  stateTrace?: StateTrace | null
  status: 'pending' | 'done' | 'error'
  error?: string | null
  /** The stored full result has been loaded; rows may still be fewer than
   * sqlRowsCount if the history store capped them. */
  fullResultLoaded?: boolean
}
