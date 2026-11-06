/**
 * The Processing queue page's state (PostgreSQL Library, §11.5).
 *
 * The page reads the queue itself, one page at a time, and every change goes
 * through the queue's own routes: upload by picker or drop, draft edit at
 * the item's revision, import (one, or the selected items as a batch) and
 * remove. A refusal that carries the item as it now is replaces the painted
 * item, so a stale revision shows the current draft rather than a guess.
 * Nothing here is cached in the browser: the queue is online-only.
 */
import { reactive } from 'vue'
import {
  deleteProcessingItem,
  importProcessingItem,
  importProcessingItems,
  listProcessingItems,
  newUploadKey,
  processingErrorMessage,
  processingRefusal,
  updateProcessingItem,
  uploadProcessingItem,
  type ProcessingDuplicates,
  type ProcessingImportResult,
  type ProcessingItem,
  type ProcessingItemEdit,
  type ProcessingItemImported,
  type ProcessingItemPage,
  type ProcessingItemUploaded,
} from '../../api/processing-items'

/** The client calls the queue makes; tests pass fakes. */
export interface ProcessingQueueApi {
  list(options: { cursor?: string | null }): Promise<ProcessingItemPage>
  upload(file: File, key: string, onProgress: (sent: number, total: number) => void): Promise<ProcessingItemUploaded>
  update(id: string, revision: number, edit: ProcessingItemEdit): Promise<ProcessingItem>
  remove(id: string): Promise<ProcessingItem>
  importOne(id: string): Promise<ProcessingItemImported>
  importMany(ids: readonly string[]): Promise<ProcessingImportResult[]>
}

export const browserProcessingQueueApi: ProcessingQueueApi = {
  list: (options) => listProcessingItems(options),
  upload: (file, key, onProgress) => uploadProcessingItem(file, key, { onProgress }),
  update: updateProcessingItem,
  remove: deleteProcessingItem,
  importOne: importProcessingItem,
  importMany: importProcessingItems,
}

/** A folder or document type a card picks from. */
export interface ProcessingQueueOption {
  value: string
  label: string
}

/** One finished import, for the page to link to the Work. */
export interface ProcessingImported {
  itemId: string
  filename: string
  workId: string
}

/** One upload's outcome, in the order the files were given. */
export interface ProcessingUploadNote {
  filename: string
  /** 'sending' while the bytes go up; an item is shown only once it is 'done'. */
  state: 'sending' | 'done' | 'failed'
  /** Bytes sent so far, as a whole percentage. */
  percent: number
  message: string
  duplicates: ProcessingDuplicates | null
}

export interface ProcessingQueueState {
  items: ProcessingItem[]
  nextCursor: string | null
  loading: boolean
  loaded: boolean
  loadError: string
  uploading: number
  uploads: ProcessingUploadNote[]
  selected: Set<string>
  busy: Set<string>
  errors: Record<string, string>
  imported: ProcessingImported[]
}

const LOAD_FAILURE = 'Could not load the Processing queue.'
const UPLOAD_FAILURE = 'Upload failed.'
const SAVE_FAILURE = 'Save failed.'
const IMPORT_FAILURE = 'Import failed.'
const REMOVE_FAILURE = 'Could not remove the item.'

export function isPdfFile(file: File): boolean {
  return file.type === 'application/pdf' || /\.pdf$/i.test(file.name)
}

export class ProcessingQueue {
  readonly state: ProcessingQueueState

  constructor(
    private readonly api: ProcessingQueueApi = browserProcessingQueueApi,
    /** Called with the number of ready items painted after every change. */
    private readonly onCount: (count: number) => void = () => {},
  ) {
    this.state = reactive<ProcessingQueueState>({
      items: [],
      nextCursor: null,
      loading: false,
      loaded: false,
      loadError: '',
      uploading: 0,
      uploads: [],
      selected: new Set(),
      busy: new Set(),
      errors: {},
      imported: [],
    }) as ProcessingQueueState
  }

  private count(): void {
    this.onCount(this.state.items.length)
  }

  private drop(id: string): void {
    this.state.items = this.state.items.filter((one) => one.id !== id)
    this.state.selected.delete(id)
    delete this.state.errors[id]
  }

  /** Paint `item` as it now is: a ready item replaces its row, a marker leaves the queue. */
  private place(item: ProcessingItem): void {
    if (item.status !== 'ready') {
      this.drop(item.id)
      return
    }
    const at = this.state.items.findIndex((one) => one.id === item.id)
    if (at === -1) this.state.items = [...this.state.items, item]
    else this.state.items.splice(at, 1, item)
  }

  /** Read the first page again, or the next one with `more`. */
  async load(more = false): Promise<void> {
    if (this.state.loading) return
    if (more && !this.state.nextCursor) return
    this.state.loading = true
    try {
      const page = await this.api.list({ cursor: more ? this.state.nextCursor : null })
      if (more) {
        const known = new Set(this.state.items.map((one) => one.id))
        this.state.items = [...this.state.items, ...page.items.filter((one) => !known.has(one.id))]
      } else {
        this.state.items = page.items
        const live = new Set(page.items.map((one) => one.id))
        for (const id of [...this.state.selected]) if (!live.has(id)) this.state.selected.delete(id)
      }
      this.state.nextCursor = page.nextCursor
      this.state.loadError = ''
      this.state.loaded = true
      this.count()
    } catch (error) {
      this.state.loadError = processingErrorMessage(error, LOAD_FAILURE)
    } finally {
      this.state.loading = false
    }
  }

  /**
   * Upload PDFs one after another, each with its own note and progress.
   * An item appears only once the queue has answered with it, so a failed
   * upload never shows one. Anything but a PDF is refused here.
   */
  async upload(files: readonly File[]): Promise<void> {
    this.state.uploads = []
    for (const file of files) {
      const at = this.state.uploads.push({ filename: file.name, state: 'sending', percent: 0, message: '', duplicates: null }) - 1
      // Reach the note through the reactive list, so progress repaints.
      const note = () => this.state.uploads[at]
      if (!isPdfFile(file)) {
        Object.assign(note(), { state: 'failed', message: 'Only PDF files can be uploaded.' })
        continue
      }
      this.state.uploading += 1
      try {
        const uploaded = await this.api.upload(file, newUploadKey(), (sent, total) => {
          note().percent = total > 0 ? Math.min(100, Math.floor((sent / total) * 100)) : 0
        })
        this.place(uploaded.item)
        const dup = uploaded.duplicates
        const hasDup = dup.items.length > 0 || dup.works.length > 0
        Object.assign(note(), {
          state: 'done',
          percent: 100,
          message: uploaded.created ? 'Added to the queue.' : 'Already in the queue.',
          duplicates: hasDup ? dup : null,
        })
      } catch (error) {
        Object.assign(note(), { state: 'failed', message: processingErrorMessage(error, UPLOAD_FAILURE) })
      } finally {
        this.state.uploading -= 1
      }
    }
    this.count()
  }

  private async act<T>(id: string, fallback: string, run: () => Promise<T>): Promise<T | null> {
    if (this.state.busy.has(id)) return null
    this.state.busy.add(id)
    delete this.state.errors[id]
    try {
      return await run()
    } catch (error) {
      const refusal = processingRefusal(error)
      if (refusal?.item) this.place(refusal.item)
      if (this.state.items.some((one) => one.id === id)) this.state.errors[id] = processingErrorMessage(error, fallback)
      this.count()
      return null
    } finally {
      this.state.busy.delete(id)
    }
  }

  /** Save the draft at the painted revision. True when it saved. */
  async save(id: string, edit: ProcessingItemEdit): Promise<boolean> {
    const item = this.state.items.find((one) => one.id === id)
    if (!item) return false
    const updated = await this.act(id, SAVE_FAILURE, () => this.api.update(id, item.revision, edit))
    if (!updated) return false
    this.place(updated)
    return true
  }

  async remove(id: string): Promise<void> {
    const marker = await this.act(id, REMOVE_FAILURE, () => this.api.remove(id))
    if (!marker) return
    this.drop(id)
    this.count()
  }

  async importOne(id: string): Promise<void> {
    const filename = this.state.items.find((one) => one.id === id)?.original_filename ?? ''
    const done = await this.act(id, IMPORT_FAILURE, () => this.api.importOne(id))
    if (!done) return
    this.drop(id)
    this.state.imported = [...this.state.imported, { itemId: id, filename, workId: done.workId }]
    this.count()
  }

  toggle(id: string, on: boolean): void {
    if (on) this.state.selected.add(id)
    else this.state.selected.delete(id)
  }

  /** Import the selected items, each on its own; a failed one stays with its message. */
  async importSelected(): Promise<void> {
    const ids = this.state.items.map((one) => one.id).filter((id) => this.state.selected.has(id))
    if (!ids.length || ids.some((id) => this.state.busy.has(id))) return
    const names = new Map(this.state.items.map((one) => [one.id, one.original_filename]))
    for (const id of ids) {
      this.state.busy.add(id)
      delete this.state.errors[id]
    }
    try {
      const results = await this.api.importMany(ids)
      for (const result of results) {
        if (result.ok && result.work_id) {
          this.drop(result.item_id)
          this.state.imported = [...this.state.imported,
            { itemId: result.item_id, filename: names.get(result.item_id) ?? '', workId: result.work_id }]
          continue
        }
        if (result.item) this.place(result.item)
        if (this.state.items.some((one) => one.id === result.item_id)) {
          this.state.errors[result.item_id] = result.message || IMPORT_FAILURE
        }
      }
    } catch (error) {
      const message = processingErrorMessage(error, IMPORT_FAILURE)
      for (const id of ids) if (this.state.items.some((one) => one.id === id)) this.state.errors[id] = message
    } finally {
      for (const id of ids) this.state.busy.delete(id)
      this.count()
    }
  }
}
