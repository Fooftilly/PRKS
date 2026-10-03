import { describe, expect, it } from 'vitest'
import { createPrksQueryClient } from './client'
import { prksQueryKeys, type PrksQueryDomain } from './keys'

const DOMAIN_KEYS: Record<PrksQueryDomain, string> = {
  performanceDiagnostics: 'performance-diagnostics',
  publishers: 'publishers',
}

describe('prksQueryKeys', () => {
  it('starts every key with its domain prefix and builds fresh arrays', () => {
    for (const [domain, prefix] of Object.entries(DOMAIN_KEYS) as [PrksQueryDomain, string][]) {
      const factory = prksQueryKeys[domain] as Record<string, () => readonly string[]>
      expect(factory.all()).toEqual([prefix])
      for (const build of Object.values(factory)) {
        const key = build()
        expect(key[0]).toBe(prefix)
        expect(key.every((part) => typeof part === 'string' && part.length > 0)).toBe(true)
        expect(build()).not.toBe(key)
      }
    }
  })

  it('invalidates a whole domain from its prefix and leaves other domains fresh', async () => {
    const client = createPrksQueryClient()
    client.setQueryData(prksQueryKeys.publishers.inUse(), [])
    client.setQueryData(prksQueryKeys.performanceDiagnostics.snapshot(), { ok: true })
    await client.invalidateQueries({ queryKey: prksQueryKeys.publishers.all() })
    expect(client.getQueryState(prksQueryKeys.publishers.inUse())?.isInvalidated).toBe(true)
    expect(client.getQueryState(prksQueryKeys.performanceDiagnostics.snapshot())?.isInvalidated).toBe(false)
  })
})
