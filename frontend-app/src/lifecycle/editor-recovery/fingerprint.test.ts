import { describe, expect, it } from 'vitest'
import { fingerprintText, sameBaseIdentity, sameBody } from './fingerprint'

// Reference values: Python mmh3.hash_bytes(text.encode('utf-16-le'), 0, x64arch=False),
// read as four little-endian uint32 words h1..h4.
const VECTORS: Array<[string, string]> = [
  ['', '00000000000000000000000000000000'],
  ['a', '033bb196ccef4fe1ccef4fe1ccef4fe1'],
  ['ab', '260fb953af22b6fdaf22b6fdaf22b6fd'],
  ['abcdefg', '851fde5ef81a9aab2a351f32fa8e8ca7'],
  ['abcdefgh', '6c3576558df898a1a76d230d42147114'],
  ['abcdefghi', '17a3d73237eee48604d39ee1ee7f6ddc'],
  ['Research note ✓ 𝄞 čćž', 'bf10ea567765a52d502fe34f868d1bb7'],
  ['x'.repeat(1000) + 'tail', '815f6bf74c1c2ff44ef3197f2fbff500'],
  ['hello world, this is a longer body\n'.repeat(37), '6f341036f0ddb32136e1ebadb63ef292'],
]

describe('fingerprintText', () => {
  it.each(VECTORS)('matches the MurmurHash3 x86_128 reference for %#', (text, expected) => {
    expect(fingerprintText(text)).toBe(expected)
  })

  it('is deterministic, 128 bits, and needs no crypto.subtle', () => {
    const subtle = Object.getOwnPropertyDescriptor(globalThis.crypto, 'subtle')
    try {
      Object.defineProperty(globalThis.crypto, 'subtle', { value: undefined, configurable: true })
      const a = fingerprintText('same text')
      expect(a).toMatch(/^[0-9a-f]{32}$/)
      expect(fingerprintText('same text')).toBe(a)
      expect(fingerprintText('same texu')).not.toBe(a)
    } finally {
      if (subtle) Object.defineProperty(globalThis.crypto, 'subtle', subtle)
    }
  })
})

describe('sameBody', () => {
  it('uses exact equality even when an injected fingerprint collides', () => {
    const collide = () => 'f'.repeat(32)
    expect(sameBody('abc', 'abd', { fingerprint: collide })).toBe(false)
    expect(sameBody('abc', 'abc', { fingerprint: collide })).toBe(true)
  })

  it('rejects on length or fingerprint mismatch before comparing', () => {
    expect(sameBody('abc', 'abcd')).toBe(false)
    expect(sameBody('abc', 'abc', { fingerprint: (t) => (t === 'abc' ? '1' : '2') })).toBe(true)
  })
})

describe('sameBaseIdentity', () => {
  const base = { revision: 4, length: 10, fingerprint: fingerprintText('0123456789') }

  it('needs revision, length and fingerprint to all match', () => {
    expect(sameBaseIdentity(base, { ...base })).toBe(true)
    expect(sameBaseIdentity(base, { ...base, revision: 5 })).toBe(false)
    expect(sameBaseIdentity(base, { ...base, length: 11 })).toBe(false)
    expect(sameBaseIdentity(base, { ...base, fingerprint: fingerprintText('012345678x') })).toBe(false)
  })

  it('never treats an unknown base as unchanged', () => {
    const unknown = { revision: null, length: null, fingerprint: null }
    expect(sameBaseIdentity(unknown, unknown)).toBe(false)
    expect(sameBaseIdentity(null, base)).toBe(false)
  })
})
