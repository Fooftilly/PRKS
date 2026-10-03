/**
 * PRKS query keys. One factory per domain; no URL strings.
 *
 * Rules:
 * - A key starts with its domain name. `prksQueryKeys.<domain>.all()` is that
 *   prefix and nothing else.
 * - A read adds segments after the prefix. The query function's parameters
 *   are the key's segments, so two reads with the same key share one request.
 * - A successful online write invalidates its domain's `all()` prefix. It does
 *   not edit cached rows; the server's answer stays canonical.
 * - Durable local-store operations never use these keys. They are not a
 *   replay queue, and nothing here is persisted.
 */
export const prksQueryKeys = {
  performanceDiagnostics: {
    all: () => ['performance-diagnostics'] as const,
    snapshot: () => ['performance-diagnostics', 'snapshot'] as const,
  },
  publishers: {
    all: () => ['publishers'] as const,
    inUse: () => ['publishers', 'in-use'] as const,
  },
} as const

export type PrksQueryDomain = keyof typeof prksQueryKeys
