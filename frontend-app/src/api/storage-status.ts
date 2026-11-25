/**
 * Typed client for GET /api/storage-status (#720), owner only.
 * Transport types come from docs/api/openapi-storage-status.json.
 * Read-only: under PostgreSQL the data root is paired with the database and
 * changes only on the host, so there is no write here. Runtime parsing still
 * checks the response shape.
 */
import { PrksApiError, prksApiRequest } from './http'
import type {
  StorageDatabase,
  StorageFilesystem,
  StorageIdentity,
  StorageObjects,
  StorageRoot,
  StorageStatus,
} from './generated/openapi'

export type { StorageDatabase, StorageFilesystem, StorageIdentity, StorageObjects, StorageRoot, StorageStatus }

const LOAD_ERROR = 'Could not load the storage status.'

const ROOT_SOURCES = ['cli', 'env', 'config_file', 'platform_default', 'development_default'] as const
const NETWORK = ['local', 'network', 'uncertain', 'unknown'] as const
const NORMALIZATION = ['preserved', 'insensitive', 'normalized'] as const

function invalid(): PrksApiError {
  return new PrksApiError(LOAD_ERROR, 200, 'invalid_response')
}

function object(value: unknown): Record<string, unknown> {
  if (value == null || typeof value !== 'object' || Array.isArray(value)) throw invalid()
  return value as Record<string, unknown>
}

function text(record: Record<string, unknown>, key: string): string {
  const value = record[key]
  if (typeof value !== 'string') throw invalid()
  return value
}

function optionalText(record: Record<string, unknown>, key: string): string | null {
  const value = record[key]
  if (value === null) return null
  if (typeof value !== 'string') throw invalid()
  return value
}

function optionalCount(record: Record<string, unknown>, key: string): number | null {
  const value = record[key]
  if (value === null) return null
  if (typeof value !== 'number' || !Number.isInteger(value) || value < 0) throw invalid()
  return value
}

function count(record: Record<string, unknown>, key: string): number {
  const value = optionalCount(record, key)
  if (value === null) throw invalid()
  return value
}

function optionalFlag(record: Record<string, unknown>, key: string): boolean | null {
  const value = record[key]
  if (value === null) return null
  if (typeof value !== 'boolean') throw invalid()
  return value
}

function oneOf<T extends string>(value: unknown, allowed: readonly T[]): T {
  if (typeof value !== 'string' || !(allowed as readonly string[]).includes(value)) throw invalid()
  return value as T
}

function parseRoot(value: unknown): StorageRoot {
  const record = object(value)
  if (record.editable_in_app !== false) throw invalid()
  return {
    configured: text(record, 'configured'),
    resolved: text(record, 'resolved'),
    source: record.source === null ? null : oneOf(record.source, ROOT_SOURCES),
    editable_in_app: false,
  }
}

function parseIdentity(value: unknown): StorageIdentity {
  const record = object(value)
  if (record.backend_type !== 'local') throw invalid()
  return {
    storage_root_id: optionalText(record, 'storage_root_id'),
    layout_version: optionalCount(record, 'layout_version'),
    state: optionalText(record, 'state'),
    relocation_state: text(record, 'relocation_state'),
    backend_type: 'local',
  }
}

function parseFilesystem(value: unknown): StorageFilesystem {
  const record = object(value)
  return {
    free_bytes: optionalCount(record, 'free_bytes'),
    total_bytes: optionalCount(record, 'total_bytes'),
    filesystem_type: optionalText(record, 'filesystem_type'),
    network: oneOf(record.network, NETWORK),
    case_sensitive: optionalFlag(record, 'case_sensitive'),
    normalization: record.normalization === null ? null : oneOf(record.normalization, NORMALIZATION),
    directory_fsync: optionalFlag(record, 'directory_fsync'),
  }
}

function parseObjects(value: unknown): StorageObjects | null {
  if (value === null) return null
  const record = object(value)
  return {
    referenced: count(record, 'referenced'),
    missing: count(record, 'missing'),
    pending_cleanup: count(record, 'pending_cleanup'),
    orphans: count(record, 'orphans'),
    held: count(record, 'held'),
  }
}

function parseDatabase(value: unknown): StorageDatabase {
  const record = object(value)
  if (record.engine !== 'PostgreSQL') throw invalid()
  return {
    engine: 'PostgreSQL',
    host: optionalText(record, 'host'),
    port: optionalText(record, 'port'),
    name: optionalText(record, 'name'),
  }
}

export function parseStorageStatus(payload: unknown): StorageStatus {
  const record = object(payload)
  return {
    root: parseRoot(record.root),
    identity: parseIdentity(record.identity),
    filesystem: parseFilesystem(record.filesystem),
    objects: parseObjects(record.objects),
    database: parseDatabase(record.database),
  }
}

export async function getStorageStatus(signal?: AbortSignal): Promise<StorageStatus> {
  return parseStorageStatus(await prksApiRequest('/api/storage-status', { signal }))
}

export function storageStatusErrorMessage(error: unknown): string {
  if (error instanceof PrksApiError && error.status === 403) {
    return 'Only the Library owner can see where the Library is stored.'
  }
  return LOAD_ERROR
}
