import { describe, expect, it, vi } from 'vitest'
import { PrksApiError } from '../../api/http'
import {
  inputFromDraft,
  openingFromSearchHash,
  openingFromView,
  saveSavedView,
  SAVED_VIEW_CREATE_FAILURE,
  SAVED_VIEW_UPDATE_FAILURE,
  stateFromOpening,
} from './editor'
import { savedViewActionMessage } from './records'

const deps = (over: Partial<Parameters<typeof saveSavedView>[1]> = {}) => ({
  create: vi.fn(async () => ({ id: 'SV-NEW' })),
  update: vi.fn(async () => ({})),
  actionMessage: savedViewActionMessage,
  ...over,
})

describe('Saved View dialog rules', () => {
  it('opens blank as an Advanced search', () => {
    expect(stateFromOpening(undefined)).toEqual({
      viewId: '',
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

  it('sends every field trimmed', () => {
    expect(
      inputFromDraft({ name: ' N ', mode: 'all', q: ' q ', tag: ' t ', author: ' a ', publisher: ' p ' }),
    ).toEqual({ name: 'N', search: { mode: 'all', q: 'q', tag: 't', author: 'a', publisher: 'p' } })
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
      openingFromView({ id: 'SV-M', name: 'Main', search: { mode: 'tag', q: '', tag: 'T', author: '', publisher: '' } }),
    ).toEqual({
      viewId: 'SV-M',
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
    const outcome = await saveSavedView(stateFromOpening({ viewId: 'SV-1', name: 'Renamed' }), d)
    expect(outcome).toEqual({ status: 'updated' })
    expect(d.update).toHaveBeenCalledWith('SV-1', expect.objectContaining({ name: 'Renamed' }))
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
    expect(await saveSavedView(stateFromOpening({ viewId: 'SV-1', name: 'X' }), lost)).toEqual({
      status: 'failed',
      message: SAVED_VIEW_UPDATE_FAILURE,
    })
  })
})
