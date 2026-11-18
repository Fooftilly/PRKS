import { inject, reactive, ref, shallowRef, type InjectionKey } from 'vue'
import type { NewFileField, SourceKind } from './save'
import type { FolderRow, PersonRow, RoleLink, VideoMeta } from './runtime'

export interface UploadRole extends RoleLink {
  /** The name shown on this file: the credit, else the profile name. */
  person_name: string
  profile_name: string
}

export interface UploadTag {
  id: string
  name: string
}

export const FIELD_CONTROL_IDS: Record<NewFileField, string> = {
  file: 'upload-drop-zone',
  videoUrl: 'work-video-url',
  folder: 'work-folder-search',
  people: 'upload-person-search',
  date: 'work-date',
  videoDate: 'work-video-published-date',
}

/** A quick-create started from one opening of the dialog. */
interface QuickCreate {
  opening: string
  done: Promise<boolean>
}

/**
 * New File's own state for one dialog. Text fields stay uncontrolled DOM
 * inputs (the dirty snapshot and the E2E harness read them there); this holds
 * what the classic module kept in globals.
 */
export function createNewFileState() {
  const kind = ref<SourceKind>('pdf')
  /** Catalogues loaded when the dialog opens. */
  const folders = shallowRef<FolderRow[]>([])
  const persons = shallowRef<PersonRow[]>([])
  const pdf = shallowRef<File | null>(null)
  const roles = ref<UploadRole[]>([])
  const tags = ref<UploadTag[]>([])
  const errors = reactive<Record<NewFileField, string>>({
    file: '',
    videoUrl: '',
    folder: '',
    people: '',
    date: '',
    videoDate: '',
  })
  const peopleError = ref('')
  const status = ref('')
  const busy = ref(false)
  const folderDefault = ref(false)
  const playlistId = ref('')
  const videoEmbedUrl = ref('')
  const counts = reactive({ biblio: 0, more: 0 })
  /** The People row whose "Name on file" editor is open, or -1. */
  const creditIdx = ref(-1)
  /** People search is held while a quick-created Person is being added. */
  const personLocked = ref(false)
  /** The opening whose Create is running, if any (reflected for the E2E harness). */
  const inFlight = ref<string | null>(null)

  let videoMeta: VideoMeta | null = null
  let lastPreviewUrl = ''
  let previewSeq = 0
  let personPending: { opening: string; done: Promise<boolean> } | null = null
  let quickCreates: QuickCreate[] = []

  function modal(): HTMLElement | null {
    const el = document.getElementById('work-modal')
    return el instanceof HTMLElement ? el : null
  }

  /** The current opening of the dialog ('' before the first). */
  function currentOpening(): string {
    return modal()?.dataset.prksOpenGeneration ?? ''
  }

  /** True while the dialog is shown for `opening`. */
  function isOpening(opening: string): boolean {
    const el = modal()
    return !!el && !el.classList.contains('hidden') && (el.dataset.prksOpenGeneration ?? '') === opening
  }

  function trackQuickCreate(task: Promise<boolean>): Promise<boolean> {
    const entry: QuickCreate = {
      opening: currentOpening(),
      done: task.then(
        (ok) => ok === true,
        () => false,
      ),
    }
    quickCreates.push(entry)
    void entry.done.then(() => {
      quickCreates = quickCreates.filter((item) => item !== entry)
    })
    return entry.done
  }

  function pendingQuickCreates(opening: string): Array<Promise<boolean>> {
    return quickCreates.filter((entry) => entry.opening === opening).map((entry) => entry.done)
  }

  function setPersonPending(opening: string, done: Promise<boolean>): void {
    const entry = { opening, done }
    personPending = entry
    const clear = () => {
      if (personPending === entry) personPending = null
    }
    void done.then(clear, clear)
  }

  function pendingPerson(opening: string): Promise<boolean> | null {
    return personPending && personPending.opening === opening ? personPending.done : null
  }

  function clearErrors(): void {
    ;(Object.keys(errors) as NewFileField[]).forEach((key) => {
      errors[key] = ''
    })
    status.value = ''
  }

  /** A new opening: nothing from the last one survives. */
  function reset(): void {
    kind.value = 'pdf'
    pdf.value = null
    roles.value = []
    tags.value = []
    clearErrors()
    peopleError.value = ''
    busy.value = false
    folderDefault.value = false
    playlistId.value = ''
    videoEmbedUrl.value = ''
    creditIdx.value = -1
    counts.biblio = 0
    counts.more = 0
    personLocked.value = false
    videoMeta = null
    lastPreviewUrl = ''
    // A quick-create still running for the previous form no longer concerns
    // this one: Create must not wait on it.
    personPending = null
    quickCreates = []
  }

  return {
    kind,
    folders,
    persons,
    pdf,
    roles,
    tags,
    errors,
    peopleError,
    status,
    busy,
    folderDefault,
    playlistId,
    videoEmbedUrl,
    counts,
    creditIdx,
    personLocked,
    currentOpening,
    isOpening,
    trackQuickCreate,
    pendingQuickCreates,
    setPersonPending,
    pendingPerson,
    clearErrors,
    reset,
    inFlight,
    getInFlight: () => inFlight.value,
    setInFlight: (opening: string | null) => {
      inFlight.value = opening
    },
    getVideoMeta: () => videoMeta,
    setVideoMeta: (meta: VideoMeta | null) => {
      videoMeta = meta
    },
    getLastPreviewUrl: () => lastPreviewUrl,
    setLastPreviewUrl: (url: string) => {
      lastPreviewUrl = url
    },
    nextPreviewSeq: () => ++previewSeq,
    isPreviewSeq: (seq: number) => seq === previewSeq,
  }
}

export type NewFileState = ReturnType<typeof createNewFileState>

export const NEW_FILE_STATE: InjectionKey<NewFileState> = Symbol('new-file-state')

export function useNewFileState(): NewFileState {
  const state = inject(NEW_FILE_STATE)
  if (!state) throw new Error('New File state is not provided')
  return state
}
