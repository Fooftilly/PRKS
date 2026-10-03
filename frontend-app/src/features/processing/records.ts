import { MutationObserver, type QueryClient } from '@tanstack/vue-query'
import { PRKS_API_FALLBACK_ERROR, PrksApiError } from '../../api/http'
import {
  importProcessingFile,
  listProcessingFiles,
  updateProcessingFile,
  type ProcessingFile,
  type ProcessingFileImported,
  type ProcessingFileUpdate,
} from '../../api/processing-files'
import { prksQueryClient, type PrksQueryMeta } from '../../query/client'
import { prksQueryKeys } from '../../query/keys'
import type { ProcessingFileDraft } from './projection'

/** Every inbox read reports its final failure under the classic source. */
export const PROCESSING_FILES_READ_META: PrksQueryMeta = { clientErrorSource: 'processing-files.fetch' }

/**
 * Files for Processing as server state: the one owner of inbox reads and file
 * writes. The classic coordinator (route load, reload, nav badge) reaches it
 * through `window.prksProcessingRecords`; the inbox card's intents call it
 * directly.
 *
 * Reads always ask the server (`staleTime: 0`) and concurrent reads of one
 * key share a request. A rescan reconciles the inbox folder on disk, so it
 * has its own key and is not retried. Writes are online-only mutations
 * without retry; every write that was sent invalidates the whole domain,
 * failed or not, because a malformed or lost reply cannot prove the server
 * did not commit. Nothing is persisted or replayed.
 */
export interface ProcessingRecords {
  /**
   * Inbox files that are not imported yet. `rescan` reconciles the inbox
   * folder first. `signal` is the reader's lifetime (a route's abort signal):
   * when it aborts, this call rejects with an AbortError, and the request is
   * cancelled only if no other reader of that key is still waiting on it.
   * A read asked for after a write was sent waits for that write to settle
   * and never answers with a request that began before it settled.
   */
  inbox(options?: { rescan?: boolean; signal?: AbortSignal }): Promise<ProcessingFile[]>
  /** Save the card's draft as the file's staged Work metadata. */
  save(fileId: string, draft: ProcessingFileDraft): Promise<ProcessingFile>
  /**
   * Import the file as a new Work from its saved metadata. After every import
   * that was sent, the offline Folder, People, Person Group, Works browse,
   * and Recently added caches are marked changed.
   */
  importFile(fileId: string): Promise<ProcessingFileImported>
  /** {@link processingActionMessage}, for classic callers. */
  actionMessage(err: unknown, fallback: string): string
}

/** Readers still waiting on each inbox key, page-wide. */
const pendingReads = new Map<string, number>()

/** Page-wide count of writes sent and settled. */
let writesSent = 0
let writesSettled = 0

/** Reads waiting for the writes sent before them to settle. */
const settleWaiters = new Set<{ count: number; resolve: () => void }>()

function untilSettled(count: number): Promise<void> {
  if (writesSettled >= count) return Promise.resolve()
  return new Promise((resolve) => {
    settleWaiters.add({ count, resolve })
  })
}

function noteWriteSettled(): void {
  writesSettled += 1
  for (const waiter of [...settleWaiters]) {
    if (writesSettled < waiter.count) continue
    settleWaiters.delete(waiter)
    waiter.resolve()
  }
}

/**
 * Writes settled when the latest request for each inbox key began, per
 * QueryClient, since each client holds its own requests.
 */
const requestStartedAfter = new WeakMap<QueryClient, Map<string, number>>()

function requestStarts(queryClient: QueryClient): Map<string, number> {
  let starts = requestStartedAfter.get(queryClient)
  if (!starts) {
    starts = new Map()
    requestStartedAfter.set(queryClient, starts)
  }
  return starts
}

function abortError(): DOMException {
  return new DOMException('Processing inbox read aborted.', 'AbortError')
}

/**
 * Text for a failed inbox read or action: the server's refusal when it sent
 * one, otherwise the action's own `fallback`.
 */
export function processingActionMessage(err: unknown, fallback: string): string {
  if (err instanceof PrksApiError && err.message.trim() && err.message !== PRKS_API_FALLBACK_ERROR) {
    return err.message.trim()
  }
  return fallback
}

/**
 * The PATCH body for a card draft. The published date goes through the shared
 * date parser, as the Work editor does; an unparseable date is sent empty.
 */
export function processingUpdateFromDraft(draft: ProcessingFileDraft): ProcessingFileUpdate {
  const parse = window.prksParsePublishedDateInput
  return {
    title: draft.title,
    status_draft: draft.status_draft,
    published_date: typeof parse === 'function' ? parse(draft.published_date) : draft.published_date,
    abstract: draft.abstract,
    source_url: draft.source_url,
    year: draft.year,
    publisher: draft.publisher,
    location: draft.location,
    edition: draft.edition,
    journal: draft.journal,
    volume: draft.volume,
    issue: draft.issue,
    pages: draft.pages,
    isbn: draft.isbn,
    doi: draft.doi,
    doc_type: draft.doc_type,
    private_notes: draft.private_notes,
    thumb_page: draft.thumb_page,
    target_folder_id: draft.target_folder_id,
    roles: draft.roles.map((role) => ({ person_id: role.person_id, role_type: role.role_type })),
    tags: draft.tags.map((tag) => ({ id: tag.id })),
  }
}

/** `queryClient` defaults to the page's shared client, resolved on each call. */
export function processingRecords(queryClient?: QueryClient): ProcessingRecords {
  const client = () => queryClient ?? prksQueryClient()

  async function write<T>(run: () => Promise<T>, settled?: () => void): Promise<T> {
    const shared = client()
    // A mutation, not a bare call, so the client's mutation defaults (no
    // retry) and its transport-failure reporting apply.
    const mutation = new MutationObserver(shared, { mutationFn: run })
    writesSent += 1
    try {
      return await mutation.mutate()
    } finally {
      noteWriteSettled()
      mutation.reset()
      settled?.()
      await shared.invalidateQueries({ queryKey: prksQueryKeys.processingFiles.all() })
    }
  }

  return {
    inbox(options = {}) {
      const signal = options.signal
      if (signal?.aborted) return Promise.reject(abortError())
      const shared = client()
      const rescan = !!options.rescan
      const queryKey = prksQueryKeys.processingFiles.inbox(rescan ? 'rescan' : 'stored')
      const slot = JSON.stringify(queryKey)
      pendingReads.set(slot, (pendingReads.get(slot) ?? 0) + 1)
      let held = true
      const release = (): boolean => {
        if (!held) return false
        held = false
        const left = (pendingReads.get(slot) ?? 1) - 1
        if (left > 0) pendingReads.set(slot, left)
        else pendingReads.delete(slot)
        return left <= 0
      }
      const fetchInbox = () =>
        shared.fetchQuery({
          queryKey,
          queryFn: ({ signal: requestSignal }) => {
            requestStarts(shared).set(slot, writesSettled)
            return listProcessingFiles({ rescan, signal: requestSignal })
          },
          staleTime: 0,
          meta: PROCESSING_FILES_READ_META,
          ...(rescan ? { retry: false } : {}),
        })
      // A read answers only from a request that began after every write
      // sent before it was asked for had settled (an import, say), as the
      // classic coordinator queued a rescan behind writes. It waits for
      // writes still in flight, and `fetchQuery` may join a request that
      // began earlier, so it reads again until that holds.
      const sentBefore = writesSent
      const read = (async () => {
        await untilSettled(sentBefore)
        if (signal?.aborted) throw abortError()
        let rows = await fetchInbox()
        while ((requestStarts(shared).get(slot) ?? -1) < sentBefore && !signal?.aborted) {
          rows = await fetchInbox()
        }
        return rows
      })()
      if (!signal) return read.finally(release)
      return new Promise<ProcessingFile[]>((resolve, reject) => {
        const onAbort = () => {
          // The last reader leaving stops the request and its retries.
          if (release()) void shared.cancelQueries({ queryKey, exact: true })
          reject(abortError())
        }
        signal.addEventListener('abort', onAbort, { once: true })
        read.then(resolve, reject).finally(() => {
          signal.removeEventListener('abort', onAbort)
          release()
        })
      })
    },
    save(fileId, draft) {
      const update = processingUpdateFromDraft(draft)
      return write(() => updateProcessingFile(fileId, update))
    },
    importFile(fileId) {
      return write(
        () => importProcessingFile(fileId),
        () => {
          try {
            window.prksMarkProcessingImportChanged?.()
          } catch {
            /* Coherence marks never hide the import's own outcome. */
          }
        },
      )
    },
    actionMessage: processingActionMessage,
  }
}
