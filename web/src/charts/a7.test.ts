import { describe, expect, it } from 'vitest'

import { validateA7Chart, withYearAxes } from './a7'

const line = (field: string, type = 'quantitative', axis?: unknown) => ({
  mark: 'line',
  data: { values: [{ [field]: 2018, revenue: 110 }, { [field]: 2023, revenue: 211 }] },
  encoding: { x: { field, type, ...(axis === undefined ? {} : { axis }) }, y: { field: 'revenue', type: 'quantitative' } },
})

describe('withYearAxes', () => {
  it('gives a quantitative year axis a plain integer format, leaving other axes alone', () => {
    const out = withYearAxes(line('year')) as { encoding: Record<string, { axis?: { format?: string } }> }
    expect(out.encoding.x.axis?.format).toBe('d')
    expect(out.encoding.y.axis).toBeUndefined()
    // The input passes A7; the axis format is added after that check, by our own code.
    expect(validateA7Chart(line('year'))).toEqual([])
  })
  it('keeps an explicit format, a hidden axis, and non-year or non-numeric fields as they are', () => {
    const own = line('fiscal_year', 'quantitative', { format: '.0f' })
    expect(withYearAxes(own)).toBe(own)
    const hidden = line('year', 'quantitative', null)
    expect(withYearAxes(hidden)).toBe(hidden)
    const ordinal = line('year', 'ordinal')
    expect(withYearAxes(ordinal)).toBe(ordinal)
    const notYear = line('yearly_total')
    expect(withYearAxes(notYear)).toBe(notYear)
  })
})
