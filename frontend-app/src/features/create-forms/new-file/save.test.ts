import { describe, expect, it } from 'vitest'
import {
  BAD_DATE,
  BAD_PDF_READ,
  BAD_YOUTUBE_URL,
  CHOOSE_PDF,
  ENRICHMENT_NOT_ADDED,
  NOTHING_SAVED,
  PERSON_NOT_ADDED,
  PERSON_NOT_PICKED,
  SERVER_FAILED,
  UNCONFIRMED_SAVE,
  saveNewFile,
  validateDraft,
  type NewFileDraft,
  type NewFileSaveDeps,
} from './save'
import { FOLDER_GONE, FOLDER_NOT_PICKED } from './folder'

function draft(overrides: Partial<NewFileDraft> = {}): NewFileDraft {
  return {
    kind: 'pdf',
    pdf: new File(['%PDF'], 'paper.pdf', { type: 'application/pdf' }),
    title: 'Paper',
    status: 'Not Started',
    docType: 'article',
    abstract: '',
    year: '',
    folderId: '',
    folderCommitted: true,
    personSearch: '',
    videoUrl: '',
    videoChannel: '',
    videoPublished: '',
    playlistId: '',
    pdfSourceUrl: '',
    pdfPublished: '',
    thumbPage: '',
    privateNotes: '',
    publisher: '',
    location: '',
    edition: '',
    journal: '',
    volume: '',
    issue: '',
    pages: '',
    isbn: '',
    doi: '',
    roles: [],
    tags: [],
    ...overrides,
  }
}

interface Recorder {
  deps: NewFileSaveDeps
  open: { value: boolean }
  busy: boolean[]
  errors: Array<[string, string]>
  focused: string[]
  status: string[]
  peopleErrors: string[]
  requests: Array<{ url: string; body: Record<string, unknown> }>
  opened: string[]
  alerts: string[]
  durable: Array<{ fields: Record<string, unknown>; tags: unknown[] }>
}

function json(status: number, body: unknown): Response {
  return new Response(JSON.stringify(body), { status, headers: { 'Content-Type': 'application/json' } })
}

function recorder(overrides: Partial<NewFileSaveDeps> = {}, input: NewFileDraft = draft()): Recorder {
  const r: Omit<Recorder, 'deps'> = {
    open: { value: true },
    busy: [],
    errors: [],
    focused: [],
    status: [],
    peopleErrors: [],
    requests: [],
    opened: [],
    alerts: [],
    durable: [],
  }
  let inFlight: string | null = null
  const deps: NewFileSaveDeps = {
    opening: '3',
    stillOpening: () => r.open.value,
    inFlight: () => inFlight,
    setInFlight: (opening) => {
      inFlight = opening
    },
    setBusy: (busy) => r.busy.push(busy),
    pendingPerson: () => null,
    pendingQuickCreates: () => [],
    readDraft: () => input,
    clearErrors: () => {},
    setFieldError: (field, message) => r.errors.push([field, message]),
    focusField: (field) => r.focused.push(field),
    setPeopleError: (message) => r.peopleErrors.push(message),
    setStatus: (message) => r.status.push(message),
    isValidYoutubeUrl: (url) => /youtube\.com\/watch\?v=/.test(url),
    parseDate: (raw) => (/^\d\d\/\d\d\/\d{4}$/.test(raw) ? `${raw.slice(6)}-${raw.slice(3, 5)}-${raw.slice(0, 2)}` : ''),
    readPdfBase64: async () => 'UERG',
    videoMeta: async () => ({ title: 'Lecture', author_name: 'Channel', thumbnail_url: 'https://i.ytimg.com/t.jpg' }),
    today: () => '2026-11-17',
    createVideoDurably: async (fields, tags) => {
      r.durable.push({ fields: fields as unknown as Record<string, unknown>, tags })
      return { create: { entity_id: 'W-VIDEO' } }
    },
    offlineRefused: () => false,
    waitForPeople: async () => true,
    request: async (url, init) => {
      r.requests.push({ url, body: JSON.parse(String(init.body || '{}')) })
      return url === '/api/works' ? json(201, { id: 'W-PDF' }) : json(200, {})
    },
    markCreated: () => {},
    failureText: (text) => `${text} Nothing was saved.`,
    closeAndOpen: (id) => r.opened.push(id),
    alert: (message) => r.alerts.push(message),
    ...overrides,
  }
  return { ...r, deps }
}

describe('validateDraft', () => {
  const rules = { isValidYoutubeUrl: (url: string) => url.includes('watch?v='), parseDate: () => '' }

  it('needs a PDF, a picked Folder, a picked person and readable dates, in reading order', () => {
    const errors = validateDraft(
      draft({ pdf: null, folderCommitted: false, personSearch: 'Typed Name', pdfPublished: '2020' }),
      rules,
    )
    expect(errors).toEqual([
      ['file', CHOOSE_PDF],
      ['folder', FOLDER_NOT_PICKED],
      ['people', PERSON_NOT_PICKED],
      ['date', BAD_DATE],
    ])
  })

  it('treats a blank People search as nothing typed', () => {
    expect(validateDraft(draft({ personSearch: '   ' }), rules)).toEqual([])
  })

  it('needs a YouTube URL for a video and ignores the PDF fields', () => {
    const errors = validateDraft(
      draft({ kind: 'video', pdf: null, videoUrl: 'https://example.com', pdfPublished: 'bad', videoPublished: 'bad' }),
      rules,
    )
    expect(errors).toEqual([
      ['videoUrl', BAD_YOUTUBE_URL],
      ['videoDate', BAD_DATE],
    ])
  })
})

describe('saveNewFile', () => {
  it('sends one PDF create with the roles, attaches tags, then opens the Work', async () => {
    const input = draft({
      roles: [{ person_id: 'P1', role_type: 'Editor', credit_name: 'A. N.' }],
      tags: [{ id: 'T1', name: 'alpha' }],
      pdfPublished: '01/02/2003',
      thumbPage: '3',
      doi: ' 10.1/x ',
    })
    const r = recorder({}, input)
    await saveNewFile(r.deps)
    expect(r.requests.map((req) => req.url)).toEqual(['/api/works', '/api/works/W-PDF/tags'])
    const body = r.requests[0].body
    expect(body.file_b64).toBe('UERG')
    expect(body.file_name).toBe('paper.pdf')
    expect(body.roles).toEqual([{ person_id: 'P1', role_type: 'Editor', credit_name: 'A. N.' }])
    expect(body.published_date).toBe('2003-02-01')
    expect(body.thumb_page).toBe(3)
    expect(body.doi).toBe('10.1/x')
    expect(body.folder_id).toBeNull()
    expect(r.requests[1].body).toEqual({ tag_id: 'T1' })
    expect(r.opened).toEqual(['W-PDF'])
    expect(r.busy).toEqual([true, false])
  })

  it('stops on validation and focuses the first invalid field', async () => {
    const r = recorder({}, draft({ pdf: null, folderCommitted: false }))
    await saveNewFile(r.deps)
    expect(r.errors.map(([field]) => field)).toEqual(['file', 'folder'])
    expect(r.focused).toEqual(['file'])
    expect(r.requests).toHaveLength(0)
    expect(r.busy).toEqual([])
  })

  it('creates a video durably with its tags and the accessed date', async () => {
    const r = recorder(
      {},
      draft({
        kind: 'video',
        pdf: null,
        videoUrl: 'https://www.youtube.com/watch?v=abc',
        playlistId: 'PL1',
        tags: [{ id: 'T2', name: 'beta' }],
        roles: [{ person_id: 'P2', role_type: '', credit_name: '' }],
      }),
    )
    await saveNewFile(r.deps)
    expect(r.requests).toHaveLength(0)
    expect(r.durable).toHaveLength(1)
    const fields = r.durable[0].fields
    expect(fields.doc_type).toBe('online')
    expect(fields.author_text).toBe('Channel')
    expect(fields.thumb_url).toBe('https://i.ytimg.com/t.jpg')
    expect(fields.urldate).toBe('2026-11-17')
    expect(fields.playlist_id).toBe('PL1')
    expect(fields.roles).toEqual([{ person_id: 'P2', role_type: 'Author', credit_name: '' }])
    expect(r.durable[0].tags).toEqual([{ id: 'T2', name: 'beta' }])
    expect(r.opened).toEqual(['W-VIDEO'])
  })

  it('uses the fetched video title when Create was pressed before the preview filled it', async () => {
    const video = { kind: 'video' as const, pdf: null, videoUrl: 'https://www.youtube.com/watch?v=abc' }
    const blank = recorder({}, draft({ ...video, title: '' }))
    await saveNewFile(blank.deps)
    expect(blank.durable[0].fields.title).toBe('Lecture')

    const typed = recorder({}, draft({ ...video, title: 'My title' }))
    await saveNewFile(typed.deps)
    expect(typed.durable[0].fields.title).toBe('My title')
  })

  it('waits for a quick-created person and refuses to create without them', async () => {
    let finish: (added: boolean) => void = () => {}
    const pending = new Promise<boolean>((resolve) => {
      finish = resolve
    })
    const r = recorder({ pendingPerson: () => pending })
    const run = saveNewFile(r.deps)
    expect(r.busy).toEqual([true])
    finish(false)
    await run
    expect(r.peopleErrors).toEqual([PERSON_NOT_ADDED])
    expect(r.status).toEqual([NOTHING_SAVED])
    expect(r.requests).toHaveLength(0)
  })

  it('refuses when a tag or playlist quick-create failed', async () => {
    const r = recorder({ pendingQuickCreates: () => [Promise.resolve(true), Promise.resolve(false)] })
    await saveNewFile(r.deps)
    expect(r.status).toEqual([ENRICHMENT_NOT_ADDED])
    expect(r.requests).toHaveLength(0)
  })

  it('drops everything once the dialog was discarded during a wait', async () => {
    let finish: (added: boolean) => void = () => {}
    const r = recorder({
      pendingPerson: () =>
        new Promise<boolean>((resolve) => {
          finish = resolve
        }),
    })
    const run = saveNewFile(r.deps)
    r.open.value = false
    finish(true)
    await run
    expect(r.requests).toHaveLength(0)
    expect(r.status).toEqual([])
    expect(r.opened).toEqual([])
  })

  it('leaves a reopened dialog alone when an earlier opening create settles', async () => {
    let current = '3'
    let release: (res: Response) => void = () => {}
    const r = recorder(
      {
        stillOpening: () => current === '3',
        request: (url, init) => {
          r.requests.push({ url, body: JSON.parse(String(init.body || '{}')) })
          if (url !== '/api/works') return Promise.resolve(json(200, {}))
          return new Promise<Response>((resolve) => {
            release = resolve
          })
        },
      },
      draft({ tags: [{ id: 'T1', name: 'a' }] }),
    )
    const run = saveNewFile(r.deps)
    await new Promise((resolve) => setTimeout(resolve, 0))
    // Closed and opened again while the create was in flight.
    current = '4'
    release(json(201, { id: 'W-OLD' }))
    await run
    // The Work exists with what was asked for, but the new form is not touched.
    expect(r.requests.map((req) => req.url)).toEqual(['/api/works', '/api/works/W-OLD/tags'])
    expect(r.opened).toEqual([])
    expect(r.status).toEqual([''])
    expect(r.alerts).toEqual([])
  })

  it('ignores a second press while this opening is creating', async () => {
    let release: (res: Response) => void = () => {}
    const r = recorder({
      request: (url, init) => {
        r.requests.push({ url, body: JSON.parse(String(init.body || '{}')) })
        return new Promise<Response>((resolve) => {
          release = resolve
        })
      },
    })
    const first = saveNewFile(r.deps)
    await new Promise((resolve) => setTimeout(resolve, 0))
    await saveNewFile(r.deps)
    expect(r.requests).toHaveLength(1)
    release(json(201, { id: 'W-1' }))
    await first
    expect(r.opened).toEqual(['W-1'])
  })

  it('says whether a Work may exist after a failure', async () => {
    const dropped = recorder({ request: () => Promise.reject(new Error('offline')) })
    await saveNewFile(dropped.deps)
    expect(dropped.status.at(-1)).toBe(UNCONFIRMED_SAVE)

    const crashed = recorder({ request: async () => json(500, { error: 'boom' }) })
    await saveNewFile(crashed.deps)
    expect(crashed.status.at(-1)).toBe(SERVER_FAILED)

    const gone = recorder({ request: async () => json(404, { error: 'Folder not found', code: 'FOLDER_NOT_FOUND' }) })
    await saveNewFile(gone.deps)
    expect(gone.status.at(-1)).toBe('Folder not found Nothing was saved.')
    expect(gone.errors).toEqual([['folder', FOLDER_GONE]])
    expect(gone.focused).toEqual(['folder'])
  })

  it('reports an unreadable PDF on the drop zone', async () => {
    const r = recorder({ readPdfBase64: () => Promise.reject(new Error('read')) })
    await saveNewFile(r.deps)
    expect(r.errors).toEqual([['file', BAD_PDF_READ]])
    expect(r.requests).toHaveLength(0)
  })

  it('opens the Work even when a tag could not be attached, and says so', async () => {
    const r = recorder(
      {
        request: async (url) => (url === '/api/works' ? json(201, { id: 'W-2' }) : json(500, {})),
      },
      draft({ tags: [{ id: 'T1', name: 'a' }, { id: 'T2', name: 'b' }] }),
    )
    await saveNewFile(r.deps)
    expect(r.opened).toEqual(['W-2'])
    expect(r.alerts[0]).toContain('2 tags could not be attached')
  })
})
