import { describe, expect, it } from 'vitest'
import { sourceLabel } from './sources'

describe('sourceLabel', () => {
  it('formats policy documents with Policy: label and capitalized remainder', () => {
    expect(sourceLabel('doc_policy_returns_refunds_c000')).toBe('Policy: Returns refunds')
  })

  it('formats SLA annexures with SLA annexure label without colon', () => {
    expect(sourceLabel('doc_sla_annexure_LP003_c001')).toBe('SLA annexure LP003')
  })

  it('formats review digest chunk refs as Review digest', () => {
    expect(
      sourceLabel('doc_review_digest_febab0275244b9a49a623f0bd613ca2f_c008')
    ).toBe('Review digest')
  })

  it('formats agreement documents with Agreement: label and preserved digits/all-caps', () => {
    expect(sourceLabel('doc_agreement_seller_S12_c003')).toBe('Agreement: Seller S12')
  })

  it('returns references not starting with doc_ unchanged', () => {
    expect(sourceLabel('chunk-7')).toBe('chunk-7')
  })

  it('formats unknown doc prefixes by humanising and capitalizing first letter', () => {
    expect(sourceLabel('doc_misc_notes_c010')).toBe('Misc notes')
  })

  it('formats contract documents with Contract: label', () => {
    expect(sourceLabel('doc_contract_vendor_terms_c005')).toBe('Contract: Vendor terms')
  })
})
