import { describe, expect, it } from 'vitest'

import { formatCellNumber } from './format'

describe('formatCellNumber', () => {
  it('shows years plainly', () => {
    expect(formatCellNumber('year', 2018)).toBe('2018')
    expect(formatCellNumber('fiscal_year', 2022)).toBe('2022')
    expect(formatCellNumber('period_end_year', 2023)).toBe('2023')
    expect(formatCellNumber('FY', 2021)).toBe('2021')
  })
  it('keeps thousands separators for quantities', () => {
    expect(formatCellNumber('revenue', 110360000000)).toBe('110,360,000,000')
    expect(formatCellNumber('years_of_service', 12)).toBe('12')
    expect(formatCellNumber('yearly_revenue', 2500)).toBe('2,500')
    expect(formatCellNumber('year', 2018.5)).toBe('2,018.5')
  })
})
