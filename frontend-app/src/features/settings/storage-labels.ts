/** Display text for the Storage settings panel (#720). Pure: no I/O. */
import type { StorageStatus } from '../../api/storage-status'

export interface StorageFactRow {
  id: string
  label: string
  value: string
}

export interface StorageRows {
  source: string
  configuredDiffers: boolean
  database: string
  objects: StorageFactRow[]
  folder: StorageFactRow[]
}

const SOURCE_LABELS: Record<string, string> = {
  cli: 'Set on the command line',
  env: 'Set by the environment',
  config_file: 'Set in the bootstrap file',
  platform_default: 'Default location',
  development_default: 'Default location',
}

const NETWORK_LABELS: Record<string, string> = {
  local: 'Local disk',
  network: 'Network filesystem',
  uncertain: 'FUSE filesystem; PRKS cannot tell whether it is local',
  unknown: 'Not known on this platform',
}

const NORMALIZATION_LABELS: Record<string, string> = {
  preserved: 'Kept as written',
  insensitive: 'Kept as written; either Unicode form finds a file',
  normalized: 'Rewritten by the filesystem',
}

const NOT_MEASURED = 'Not measured'

export function formatStorageBytes(bytes: number): string {
  const units = ['B', 'KB', 'MB', 'GB', 'TB', 'PB']
  let value = bytes
  let unit = 0
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024
    unit += 1
  }
  return unit === 0 ? `${bytes} B` : `${value.toFixed(value < 10 ? 1 : 0)} ${units[unit]}`
}

function yesNo(value: boolean | null, yes: string, no: string): string {
  if (value === null) return NOT_MEASURED
  return value ? yes : no
}

function databaseText(status: StorageStatus): string {
  const { host, port, name } = status.database
  const server = host ? (port ? `${host}:${port}` : host) : port ? `port ${port}` : null
  const where = [server, name].filter(Boolean).join('/')
  return where ? `PostgreSQL · ${where}` : 'PostgreSQL'
}

export function storageRows(status: StorageStatus): StorageRows {
  const objects = status.objects
  const fs = status.filesystem
  const identity = status.identity
  return {
    source: status.root.source ? (SOURCE_LABELS[status.root.source] ?? 'Set on the host') : 'Set on the host',
    configuredDiffers: status.root.configured !== status.root.resolved,
    database: databaseText(status),
    objects: objects
      ? [
          { id: 'referenced', label: 'Files the Library names', value: String(objects.referenced) },
          { id: 'missing', label: 'Missing from the folder', value: String(objects.missing) },
          { id: 'pending_cleanup', label: 'Waiting for cleanup', value: String(objects.pending_cleanup) },
          { id: 'orphans', label: 'Unused files waiting for cleanup', value: String(objects.orphans) },
          { id: 'held', label: 'Kept by a restore', value: String(objects.held) },
        ]
      : [{ id: 'unavailable', label: 'File counts', value: 'Could not be read' }],
    folder: [
      { id: 'storage_root_id', label: 'Storage ID', value: identity.storage_root_id ?? 'Marker unreadable' },
      { id: 'layout_version', label: 'Layout version', value: identity.layout_version === null ? 'Unknown' : String(identity.layout_version) },
      { id: 'relocation_state', label: 'Storage move', value: identity.relocation_state === 'none' ? 'None' : identity.relocation_state },
      { id: 'filesystem_type', label: 'Filesystem', value: fs.filesystem_type ?? 'Unknown' },
      { id: 'network', label: 'Disk', value: NETWORK_LABELS[fs.network] ?? fs.network },
      { id: 'case_sensitive', label: 'Upper and lower case names', value: yesNo(fs.case_sensitive, 'Different files', 'Same file') },
      { id: 'normalization', label: 'Unicode names', value: fs.normalization ? (NORMALIZATION_LABELS[fs.normalization] ?? fs.normalization) : NOT_MEASURED },
      { id: 'directory_fsync', label: 'Directory sync', value: yesNo(fs.directory_fsync, 'Supported', 'Not supported') },
    ],
  }
}
