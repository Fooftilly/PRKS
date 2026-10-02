import {
  normalizeProcessingFolders,
  normalizeProcessingPeople,
  normalizeProcessingTags,
  type ProcessingFileDraft,
  type ProcessingFolder,
  type ProcessingPerson,
  type ProcessingResume,
  type ProcessingTagOption,
} from './projection'

/** Owning TabContext fields Processing intents need. Not a second route model. */
export interface ProcessingIntentOwner {
  tabId?: string
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
  root?: HTMLElement | null
}

export type ProcessingActionOutcome =
  | { status: 'success' }
  | { status: 'quiet' }
  | { status: 'error'; message: string }

export type ProcessingPreviewPlacement = 'card' | 'side' | 'unavailable'

export interface ProcessingPreviewFile {
  id: string
  filename: string
  relPath: string
  canPreview: boolean
}

export interface ProcessingPersonCreated {
  id: string
  name: string
  people: ProcessingPerson[]
}

export interface ProcessingFolderCreated {
  id: string
  title: string
  /** Null when the folder read failed. The painted list stays the catalogue. */
  folders: ProcessingFolder[] | null
  foldersFailed?: boolean
}

export interface ProcessingIntents {
  reload(resume: ProcessingResume | null): Promise<ProcessingActionOutcome>
  save(fileId: string, draft: ProcessingFileDraft): Promise<ProcessingActionOutcome>
  importFile(fileId: string, draft: ProcessingFileDraft, resume: ProcessingResume | null): Promise<ProcessingActionOutcome>
  searchTags(): Promise<ProcessingTagOption[] | null>
  createTag(name: string): Promise<{ id: string; name: string } | null>
  quickCreateFolder(title: string): Promise<ProcessingFolderCreated | null>
  quickCreatePerson(name: string): Promise<ProcessingPersonCreated | null>
  attachResources(host: HTMLElement): void
  releaseResources(): void
  setPreview(file: ProcessingPreviewFile): ProcessingPreviewPlacement
}

const SAVE_FAILURE = 'Save failed.'
const IMPORT_FAILURE = 'Import failed.'
const REFRESH_FAILURE = 'Could not refresh files for processing.'

function quiet(): ProcessingActionOutcome {
  return { status: 'quiet' }
}

function success(): ProcessingActionOutcome {
  return { status: 'success' }
}

function failure(message: string): ProcessingActionOutcome {
  return { status: 'error', message }
}

function actionMessage(err: unknown, fallback: string): string {
  if (err instanceof Error && err.message.trim()) return err.message.trim()
  return fallback
}

export function ownsProcessing(
  owner: ProcessingIntentOwner | null | undefined,
  generation: number,
): boolean {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!route && route.name === 'processing-files'
}

async function reloadIfCurrent(
  owner: ProcessingIntentOwner | null,
  generation: number,
  resume: ProcessingResume | null,
): Promise<ProcessingActionOutcome> {
  if (!owner || !ownsProcessing(owner, generation)) return quiet()
  const reload = window.prksReloadProcessingFiles
  if (typeof reload !== 'function') return quiet()
  try {
    const painted = await reload(owner, generation, resume)
    if (!ownsProcessing(owner, generation)) return quiet()
    if (typeof painted === 'string' && painted.trim()) return failure(painted.trim())
    if (painted !== true) return quiet()
    return success()
  } catch (err) {
    if (!ownsProcessing(owner, generation)) return quiet()
    return failure(actionMessage(err, REFRESH_FAILURE))
  }
}

/**
 * Inbox writes stay on the classic upload-style wrappers. There is no
 * processing-file durable queue. Preview and the resize listener are the
 * same module: Vue asks, the coordinator owns the iframe and the listener.
 * `still` is this owner's generation.
 */
export function browserProcessingIntents(
  owner: ProcessingIntentOwner | null,
  generation: number,
): ProcessingIntents {
  return {
    reload(resume) {
      return reloadIfCurrent(owner, generation, resume)
    },
    async save(fileId, draft) {
      if (!ownsProcessing(owner, generation)) return quiet()
      const save = window.prksProcessingSave
      if (typeof save !== 'function') return failure(SAVE_FAILURE)
      try {
        await save(fileId, draft)
      } catch (err) {
        if (!ownsProcessing(owner, generation)) return quiet()
        return failure(actionMessage(err, SAVE_FAILURE))
      }
      if (!ownsProcessing(owner, generation)) return quiet()
      return success()
    },
    async importFile(fileId, draft, resume) {
      if (!ownsProcessing(owner, generation)) return quiet()
      const save = window.prksProcessingSave
      const importFile = window.prksProcessingImport
      if (typeof save !== 'function' || typeof importFile !== 'function') return failure(IMPORT_FAILURE)
      try {
        await save(fileId, draft)
        if (!ownsProcessing(owner, generation)) return quiet()
        await importFile(fileId)
      } catch (err) {
        if (!ownsProcessing(owner, generation)) return quiet()
        return failure(actionMessage(err, IMPORT_FAILURE))
      }
      return reloadIfCurrent(owner, generation, resume)
    },
    async searchTags() {
      if (!ownsProcessing(owner, generation)) return null
      const search = window.prksProcessingSearchTags
      if (typeof search !== 'function') return null
      const rows = await search()
      if (!ownsProcessing(owner, generation)) return null
      return normalizeProcessingTags(rows)
    },
    async createTag(name) {
      if (!ownsProcessing(owner, generation)) return null
      const create = window.prksProcessingCreateTag
      if (typeof create !== 'function') return null
      try {
        const created = await create(name)
        if (!ownsProcessing(owner, generation)) return null
        if (!created || !created.id) return null
        return { id: String(created.id), name: String(created.name || name) }
      } catch (err) {
        if (!ownsProcessing(owner, generation)) return null
        const alertFn = window.prksAlertMessage
        if (typeof alertFn === 'function') {
          await alertFn(actionMessage(err, 'Could not create tag.'), 'Error')
        }
        return null
      }
    },
    async quickCreateFolder(title) {
      if (!ownsProcessing(owner, generation)) return null
      const create = window.prksProcessingQuickCreateFolder
      if (typeof create !== 'function') return null
      const created = await create(title)
      if (!ownsProcessing(owner, generation)) return null
      if (!created || !created.ok || !created.id) return null
      return {
        id: String(created.id),
        title: String(created.title || title),
        folders: created.foldersFailed ? null : normalizeProcessingFolders(created.folders),
        foldersFailed: !!created.foldersFailed,
      }
    },
    async quickCreatePerson(name) {
      if (!ownsProcessing(owner, generation)) return null
      const create = window.prksProcessingQuickCreatePerson
      if (typeof create !== 'function') return null
      const created = await create(name)
      if (!ownsProcessing(owner, generation)) return null
      if (!created || !created.id) return null
      return {
        id: String(created.id),
        name: String(created.name || name),
        people: normalizeProcessingPeople(created.people),
      }
    },
    attachResources(host) {
      if (!owner) return
      window.prksProcessingAttachResources?.(owner, host)
    },
    releaseResources() {
      if (!owner) return
      window.prksProcessingReleaseResources?.(owner)
    },
    setPreview(file) {
      if (!ownsProcessing(owner, generation) || !owner) return 'unavailable'
      const placement = window.prksProcessingSetPreview?.(owner, file)
      if (placement === 'card' || placement === 'side' || placement === 'unavailable') return placement
      return 'unavailable'
    },
  }
}
