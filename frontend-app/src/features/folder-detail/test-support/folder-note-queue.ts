/**
 * A minimal durable queue for Folder Reminders tests (#534): `window.prksSync`
 * reduced to what Folder fields use, with the local store's coalescing and
 * scope_busy rules for SET_FOLDER_FIELD (`saveFolderFields`). An
 * acknowledgement updates `server` and is emitted as the sync runtime emits it.
 */
export type FolderRow = {
  op_id: string
  sequence: number
  operation: 'SET_FOLDER_FIELD'
  entity_type: 'folder'
  entity_id: string
  payload: { field: string; value: string }
  base_revision: number
  status: string
  attempt_count: number
}

export type FolderServer = Record<string, { private_notes: string; revision: number; title?: string }>

type Observed = { value: string; revision: number }

export function createFolderQueue(server: FolderServer) {
  let rows: FolderRow[] = []
  let seq = 0
  let failNext = 0
  const listeners = new Set<(event: unknown) => void>()
  const saves: { folderId: string; changes: Record<string, string>; base: Record<string, Observed> }[] = []
  const busy = (row: FolderRow) =>
    Object.assign(new Error('This field is syncing or needs resolution.'), {
      prksLocalStoreCode: 'scope_busy',
      prksBusyOpId: row.op_id,
      prksBusyStatus: row.status,
    })
  const store = {
    async saveFolderFields(folderId: string, changes: Record<string, string>, base: Record<string, Observed>) {
      saves.push({ folderId, changes: { ...changes }, base: JSON.parse(JSON.stringify(base)) })
      if (failNext > 0) {
        failNext -= 1
        throw Object.assign(new Error('refused'), { prksLocalStoreCode: 'write_failed' })
      }
      const written: FolderRow[] = []
      for (const field of Object.keys(changes)) {
        const desired = changes[field]
        const observed = base[field]
        const existing = rows.find((r) => r.entity_id === folderId && r.payload.field === field)
        if (existing) {
          if (existing.status !== 'pending' || existing.attempt_count > 0) throw busy(existing)
          if (existing.payload.value === desired) {
            written.push(existing)
            continue
          }
          rows = rows.filter((r) => r !== existing)
        }
        if (desired === observed.value) continue
        const row: FolderRow = {
          op_id: 'op-' + ++seq,
          sequence: seq,
          operation: 'SET_FOLDER_FIELD',
          entity_type: 'folder',
          entity_id: folderId,
          payload: { field, value: desired },
          base_revision: observed.revision,
          status: 'pending',
          attempt_count: 0,
        }
        rows.push(row)
        written.push(row)
      }
      return written
    },
    async listOperations() {
      return rows.map((r) => ({ ...r, payload: { ...r.payload } }))
    },
  }
  const emit = (event: unknown) => listeners.forEach((fn) => fn(event))
  return {
    store,
    saves,
    subscribe(fn: (event: unknown) => void) {
      listeners.add(fn)
      return () => listeners.delete(fn)
    },
    changed() {
      emit({})
    },
    rows: () => rows,
    reset() {
      rows = []
      seq = 0
      failNext = 0
      saves.length = 0
    },
    failNext(n: number) {
      failNext = n
    },
    /** The row was sent: it can no longer be rewritten or withdrawn. */
    attempt(opId: string) {
      rows.find((r) => r.op_id === opId)!.attempt_count = 1
    },
    /** The server acknowledged the row at `revision`; what it stores is trimmed. */
    ack(opId: string, revision: number) {
      const row = rows.find((r) => r.op_id === opId)!
      rows = rows.filter((r) => r !== row)
      if (row.payload.field === 'private_notes') {
        server[row.entity_id] = { ...server[row.entity_id], private_notes: row.payload.value.trim(), revision }
      }
      emit({
        acknowledged: {
          code: 'ACKNOWLEDGED', folder_id: row.entity_id, field: row.payload.field,
          changed: true, server_revision: revision, value_omitted: true,
        },
        operation: row.operation,
        op: row,
      })
    },
    /** Replays an acknowledgement already delivered (a late duplicate). */
    replayAck(row: FolderRow, revision: number) {
      emit({
        acknowledged: {
          code: 'ACKNOWLEDGED', folder_id: row.entity_id, field: row.payload.field,
          changed: true, server_revision: revision, value_omitted: true,
        },
        operation: row.operation,
        op: row,
      })
    },
    conflict(opId: string) {
      rows.find((r) => r.op_id === opId)!.status = 'conflict'
      emit({})
    },
    /** Another tab or device queued a row for this Folder. */
    foreign(folderId: string, field: string, value: string, revision: number) {
      const row: FolderRow = {
        op_id: 'op-foreign-' + ++seq,
        sequence: seq,
        operation: 'SET_FOLDER_FIELD',
        entity_type: 'folder',
        entity_id: folderId,
        payload: { field, value },
        base_revision: revision,
        status: 'pending',
        attempt_count: 0,
      }
      rows.push(row)
      return row
    },
  }
}
