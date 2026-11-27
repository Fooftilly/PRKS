import { describe, expect, it, vi } from 'vitest'
import { PrksApiError } from '../../api/http'
import {
  inputFromDraft,
  openingFromSearchHash,
  openingFromView,
  saveSavedView,
  SAVED_VIEW_CHANGED_ELSEWHERE,
  SAVED_VIEW_CREATE_FAILURE,
  SAVED_VIEW_UPDATE_FAILURE,
  stateFromOpening,
} from './editor'
import { savedViewActionMessage } from './records'

const deps = (over: Partial<Parameters<typeof saveSavedView>[1]> = {}) => ({
  create: vi.fn(async () => ({ id: 'SV-NEW' })),
  update: vi.fn(async () => ({})),
  read: vi.fn(async () => ({ revision: 5 })),
  actionMessage: savedViewActionMessage,
  ...over,
})

describe('Saved View dialog rules', () => {
  it('opens blank as an Advanced search', () => {
    expect(stateFromOpening(undefined)).toEqual({
      viewId: '',
      baseRevision: null,
      draft: { name: '', mode: 'advanced', q: '', tag: '', author: '', publisher: '' },
    })
  })

  it('fills an edit from its record', () => {
    const state = stateFromOpening({
      viewId: 'SV-1',
      name: 'Critical Theory',
      definition: { mode: 'tag', tag: 'T', author: 'Adorno' },
    })
    expect(state.viewId).toBe('SV-1')
    expect(state.draft).toMatchObject({ name: 'Critical Theory', mode: 'tag', tag: 'T', author: 'Adorno', q: '' })
  })

  it('reads an unknown mode as Advanced', () => {
    expect(stateFromOpening({ definition: { mode: 'bogus' } }).draft.mode).toBe('advanced')
  })

  it('sends only the fields the mode shows, trimmed', () => {
    const draft = { name: ' N ', q: ' q ', tag: ' t ', author: ' a ', publisher: ' p ' }
    expect(inputFromDraft({ ...draft, mode: 'all' })).toEqual({
      name: 'N',
      search: { mode: 'all', q: 'q', tag: '', author: '', publisher: '' },
    })
    expect(inputFromDraft({ ...draft, mode: 'advanced' }).search).toEqual({
      mode: 'advanced',
      q: 'q',
      tag: '',
      author: 'a',
      publisher: 'p',
    })
    expect(inputFromDraft({ ...draft, mode: 'tag' }).search).toEqual({
      mode: 'tag',
      q: '',
      tag: 't',
      author: 'a',
      publisher: 'p',
    })
  })

  it('takes a Search hash through the route model and codec', () => {
    expect(openingFromSearchHash('#/search?q=owned&author=Benjamin')).toEqual({
      ok: true,
      opening: { definition: { mode: 'advanced', q: 'owned', tag: '', author: 'Benjamin', publisher: '' } },
    })
    expect(openingFromSearchHash('#/search?q=x&any=1')).toMatchObject({
      ok: true,
      opening: { definition: { mode: 'all', q: 'x' } },
    })
  })

  it('refuses a search that cannot be saved', () => {
    expect(openingFromSearchHash('#/search?q=x&tag=y')).toEqual({
      ok: false,
      message: 'This search combination cannot be saved as a view.',
    })
    expect(openingFromSearchHash('#/folders').ok).toBe(false)
  })

  it('edits the current view only when there is one', () => {
    expect(openingFromView(null)).toBeNull()
    expect(openingFromView({ name: 'x' })).toBeNull()
    expect(
      openingFromView({ id: 'SV-M', name: 'Main', search: { mode: 'tag', q: '', tag: 'T', author: '', publisher: '' }, revision: 7 }),
    ).toEqual({
      viewId: 'SV-M',
      baseRevision: 7,
      name: 'Main',
      definition: { mode: 'tag', q: '', tag: 'T', author: '', publisher: '' },
    })
  })

  it('refuses a blank name before sending', async () => {
    const d = deps()
    const outcome = await saveSavedView(stateFromOpening({ name: '   ' }), d)
    expect(outcome).toEqual({ status: 'name-required' })
    expect(d.create).not.toHaveBeenCalled()
  })

  it('creates and returns the new id', async () => {
    const d = deps()
    const outcome = await saveSavedView(
      stateFromOpening({ name: 'Adorno', definition: { mode: 'all', q: 'critical theory' } }),
      d,
    )
    expect(outcome).toEqual({ status: 'created', id: 'SV-NEW' })
    expect(d.create).toHaveBeenCalledWith({
      name: 'Adorno',
      search: { mode: 'all', q: 'critical theory', tag: '', author: '', publisher: '' },
    })
  })

  it('updates the same view', async () => {
    const d = deps()
    const outcome = await saveSavedView(stateFromOpening({ viewId: 'SV-1', baseRevision: 2, name: 'Renamed' }), d)
    expect(outcome).toEqual({ status: 'updated' })
    expect(d.update).toHaveBeenCalledWith('SV-1', expect.objectContaining({ name: 'Renamed' }), 2)
    expect(d.create).not.toHaveBeenCalled()
  })

  it('shows the server refusal, else the action fallback', async () => {
    const refused = deps({
      create: vi.fn(async () => {
        throw new PrksApiError('A Saved View with that name already exists.', 409, 'conflict')
      }),
    })
    expect(await saveSavedView(stateFromOpening({ name: 'Taken' }), refused)).toEqual({
      status: 'failed',
      message: 'A Saved View with that name already exists.',
    })
    const lost = deps({
      create: vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
      update: vi.fn(async () => {
        throw new TypeError('Failed to fetch')
      }),
    })
    expect(await saveSavedView(stateFromOpening({ name: 'X' }), lost)).toEqual({
      status: 'failed',
      message: SAVED_VIEW_CREATE_FAILURE,
    })
    expect(await saveSavedView(stateFromOpening({ viewId: 'SV-1', baseRevision: 0, name: 'X' }), lost)).toEqual({
      status: 'failed',
      message: SAVED_VIEW_UPDATE_FAILURE,
    })
  })

  it('turns a stale edit into a decision and measures the next Save against the current view (#873)', async () => {
    const d = deps({
      update: vi.fn(async () => {
        throw new PrksApiError('This Saved View changed elsewhere.', 409, 'revision_conflict')
      }),
      read: vi.fn(async () => ({ revision: 6 })),
    })
    const outcome = await saveSavedView(stateFromOpening({ viewId: 'SV-1', baseRevision: 4, name: 'Mine' }), d)
    expect(outcome).toEqual({ status: 'conflict', message: SAVED_VIEW_CHANGED_ELSEWHERE, baseRevision: 6 })
    expect(d.update).toHaveBeenCalledWith('SV-1', expect.objectContaining({ name: 'Mine' }), 4)
    expect(d.read).toHaveBeenCalledWith('SV-1')

    const gone = deps({ update: d.update, read: vi.fn(async () => null) })
    expect(await saveSavedView(stateFromOpening({ viewId: 'SV-1', baseRevision: 4, name: 'Mine' }), gone)).toEqual({
      status: 'failed',
      message: 'This Saved View changed elsewhere.',
    })
    expect(await saveSavedView(stateFromOpening({ viewId: 'SV-1', name: 'No base' }), deps())).toEqual({
      status: 'failed',
      message: SAVED_VIEW_UPDATE_FAILURE,
    })
  })
})
