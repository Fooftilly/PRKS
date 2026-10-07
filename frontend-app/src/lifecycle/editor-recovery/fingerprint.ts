/**
 * Deterministic 128-bit text fingerprint and exact body equality.
 *
 * MurmurHash3 x86_128 (seed 0) over the UTF-16LE bytes of the string, rendered
 * as 32 hex characters (h1..h4). Pure JavaScript, so it works where
 * `crypto.subtle` is missing (LAN/HTTP). It is not a security primitive and it
 * never decides a delete on its own: it identifies a base that is not
 * retained, and it is a precheck before exact `===` comparison. Callers compute
 * it once per base, never per keystroke.
 */

const C1 = 0x239b961b
const C2 = 0xab0e9789
const C3 = 0x38b34ae5
const C4 = 0xa1e38b93

function rotl(x: number, r: number): number {
  return (x << r) | (x >>> (32 - r))
}

/** Sum modulo 2^32, as the C reference's uint32 arithmetic (not a truncation). */
function add32(...terms: number[]): number {
  let sum = 0
  for (const t of terms) sum = (sum + t) >>> 0
  return sum
}

function fmix(h: number): number {
  h ^= h >>> 16
  h = Math.imul(h, 0x85ebca6b)
  h ^= h >>> 13
  h = Math.imul(h, 0xc2b2ae35)
  h ^= h >>> 16
  return h
}

function hex(h: number): string {
  return (h >>> 0).toString(16).padStart(8, '0')
}

export function fingerprintText(text: string): string {
  const units = text.length
  const blocks = units >>> 3
  let h1 = 0
  let h2 = 0
  let h3 = 0
  let h4 = 0

  for (let i = 0; i < blocks; i++) {
    const o = i << 3
    let k1 = text.charCodeAt(o) | (text.charCodeAt(o + 1) << 16)
    let k2 = text.charCodeAt(o + 2) | (text.charCodeAt(o + 3) << 16)
    let k3 = text.charCodeAt(o + 4) | (text.charCodeAt(o + 5) << 16)
    let k4 = text.charCodeAt(o + 6) | (text.charCodeAt(o + 7) << 16)

    k1 = Math.imul(rotl(Math.imul(k1, C1), 15), C2)
    h1 ^= k1
    h1 = rotl(h1, 19)
    h1 = add32(h1, h2)
    h1 = add32(Math.imul(h1, 5), 0x561ccd1b)

    k2 = Math.imul(rotl(Math.imul(k2, C2), 16), C3)
    h2 ^= k2
    h2 = rotl(h2, 17)
    h2 = add32(h2, h3)
    h2 = add32(Math.imul(h2, 5), 0x0bcaa747)

    k3 = Math.imul(rotl(Math.imul(k3, C3), 17), C4)
    h3 ^= k3
    h3 = rotl(h3, 15)
    h3 = add32(h3, h4)
    h3 = add32(Math.imul(h3, 5), 0x96cd1c35)

    k4 = Math.imul(rotl(Math.imul(k4, C4), 18), C1)
    h4 ^= k4
    h4 = rotl(h4, 13)
    h4 = add32(h4, h1)
    h4 = add32(Math.imul(h4, 5), 0x32ac3b17)
  }

  // Tail: the remaining code units as little-endian bytes (always an even count).
  const tailStart = blocks << 3
  const tailBytes = (units - tailStart) * 2
  const byteAt = (j: number): number => {
    const unit = text.charCodeAt(tailStart + (j >>> 1))
    return j & 1 ? unit >>> 8 : unit & 0xff
  }
  let k1 = 0
  let k2 = 0
  let k3 = 0
  let k4 = 0
  for (let j = tailBytes - 1; j >= 0; j--) {
    const b = byteAt(j) << ((j & 3) * 8)
    if (j >= 12) k4 ^= b
    else if (j >= 8) k3 ^= b
    else if (j >= 4) k2 ^= b
    else k1 ^= b
  }
  if (tailBytes > 12) {
    k4 = Math.imul(rotl(Math.imul(k4, C4), 18), C1)
    h4 ^= k4
  }
  if (tailBytes > 8) {
    k3 = Math.imul(rotl(Math.imul(k3, C3), 17), C4)
    h3 ^= k3
  }
  if (tailBytes > 4) {
    k2 = Math.imul(rotl(Math.imul(k2, C2), 16), C3)
    h2 ^= k2
  }
  if (tailBytes > 0) {
    k1 = Math.imul(rotl(Math.imul(k1, C1), 15), C2)
    h1 ^= k1
  }

  // Byte length; `>>> 0` keeps the low 32 bits like the C reference's uint32 cast.
  const len = (units * 2) >>> 0
  h1 ^= len
  h2 ^= len
  h3 ^= len
  h4 ^= len
  h1 = add32(h1, h2, h3, h4)
  h2 = add32(h2, h1)
  h3 = add32(h3, h1)
  h4 = add32(h4, h1)
  h1 = fmix(h1)
  h2 = fmix(h2)
  h3 = fmix(h3)
  h4 = fmix(h4)
  h1 = add32(h1, h2, h3, h4)
  h2 = add32(h2, h1)
  h3 = add32(h3, h1)
  h4 = add32(h4, h1)
  return hex(h1) + hex(h2) + hex(h3) + hex(h4)
}

export type Fingerprinter = (text: string) => string

export interface BodyComparison {
  /** Optional fast precheck. A match never decides equality; only `===` does. */
  fingerprint?: Fingerprinter
}

/**
 * Exact body equality: length precheck, optional fingerprint precheck, then
 * `===`. A colliding fingerprint can only make this slower, never true.
 */
export function sameBody(a: string, b: string, options: BodyComparison = {}): boolean {
  if (a.length !== b.length) return false
  if (options.fingerprint && options.fingerprint(a) !== options.fingerprint(b)) return false
  return a === b
}

export interface BaseIdentity {
  revision: number | null
  length: number | null
  fingerprint: string | null
}

/**
 * A base that is not retained is unchanged only when revision, length and
 * fingerprint all match. Revision is primary; the fingerprint catches a
 * revision that was reused or regressed (for example a restored backup).
 */
export function sameBaseIdentity(a: BaseIdentity | null, b: BaseIdentity | null): boolean {
  if (!a || !b) return false
  if (a.revision === null || b.revision === null) return false
  return a.revision === b.revision && a.length === b.length && a.fingerprint !== null && a.fingerprint === b.fingerprint
}
