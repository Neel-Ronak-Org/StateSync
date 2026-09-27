function humanise(str: string): string {
  const tokens = str.split('_').filter(Boolean)
  return tokens
    .map((token) => {
      if (/\d/.test(token) || (token === token.toUpperCase() && /[A-Z]/.test(token))) {
        return token
      }
      return token.toLowerCase()
    })
    .join(' ')
}

function capitalizeFirst(text: string): string {
  if (!text) return ''
  return text.charAt(0).toUpperCase() + text.slice(1)
}

const KNOWN_PREFIXES = [
  { prefix: 'policy_', label: 'Policy: ', capitalizeRemainder: true },
  { prefix: 'sla_annexure_', label: 'SLA annexure ', capitalizeRemainder: false },
  { prefix: 'agreement_', label: 'Agreement: ', capitalizeRemainder: true },
  { prefix: 'contract_', label: 'Contract: ', capitalizeRemainder: true },
] as const

export function sourceLabel(ref: string): string {
  if (!ref.startsWith('doc_')) {
    return ref
  }

  let rest = ref.slice(4)
  rest = rest.replace(/_c\d+$/, '')

  if (rest.startsWith('review_digest_')) {
    return 'Review digest'
  }

  for (const { prefix, label, capitalizeRemainder } of KNOWN_PREFIXES) {
    if (rest.startsWith(prefix)) {
      const remainder = rest.slice(prefix.length)
      let humanised = humanise(remainder)
      if (capitalizeRemainder) {
        humanised = capitalizeFirst(humanised)
      }
      return label + humanised
    }
  }

  return capitalizeFirst(humanise(rest))
}
