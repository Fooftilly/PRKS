import { describe, expect, it } from 'vitest'
import {
  acceptArgumentDetail,
  buildArgumentDetailProjection,
  buildArgumentIndexProjection,
  argumentEditorDraftFromForm,
  createEditorRowKeys,
  draftFromArgument,
} from './projection'

describe('Argument projections', () => {
  it('keeps a locally created Argument with no server lists', () => {
    const detail = acceptArgumentDetail({
      id: 'A-local',
      name: 'Unsent',
      kind: 'stance',
      main_text: 'draft body',
    })
    expect(detail).toMatchObject({
      id: 'A-local',
      kind: 'stance',
      main_text: 'draft body',
      targets: [],
      sources: [],
      responses: [],
      mentions: [],
      verdicts: [],
    })
  })

  it('preserves overlaid names and work titles already applied by the coordinator', () => {
    const projection = buildArgumentDetailProjection({
      availability: 'ready',
      generation: 3,
      argument: {
        id: 'A1',
        name: 'Pending rename',
        kind: 'argument',
        main_text: 'after',
        targets: [
          { type: 'position', id: 'P1', name: 'Pending position', verdict_id: 'supports' },
          { type: 'argument', id: 'A2', name: 'Pending argument', kind: 'stance', verdict_id: 'opposes' },
        ],
        sources: [{ work_id: 'W1', work_title: 'Renamed work', pages: '1' }],
        responses: [{ id: 'A3', name: 'Pending response', kind: 'argument' }],
        mentions: [{ work_id: 'W2', title: 'Mention title' }],
      },
    })
    expect(projection.argument?.targets.map((row) => row.name)).toEqual([
      'Pending position',
      'Pending argument',
    ])
    expect(projection.argument?.sources[0]?.work_title).toBe('Renamed work')
    expect(projection.argument?.responses[0]?.name).toBe('Pending response')
    expect(projection.argument?.mentions[0]?.title).toBe('Mention title')
  })

  it('drops rows on unavailable index and distinguishes detail outcomes', () => {
    const index = buildArgumentIndexProjection({
      availability: 'unavailable',
      kind: 'stance',
      items: [{ id: 'A1', name: 'Hidden' }],
      generation: 1,
    })
    expect(index.items).toEqual([])
    expect(index.kind).toBe('stance')
    expect(
      buildArgumentDetailProjection({ availability: 'not-found', argument: { id: 'A1' }, generation: 2 })
        .argument,
    ).toBeNull()
    expect(
      buildArgumentDetailProjection({
        availability: 'unavailable',
        argument: { id: 'A1', name: 'Cached' },
        generation: 3,
      }).availability,
    ).toBe('unavailable')
  })

  it('copies an edit draft without sharing the projection arrays', () => {
    const detail = acceptArgumentDetail({
      id: 'A1',
      name: 'Name',
      kind: 'argument',
      main_text: 'Body',
      targets: [{ type: 'position', id: 'P1', name: 'P', verdict_id: 'supports' }],
      sources: [{ work_id: 'W1', work_title: 'Work', pages: '2' }],
    })
    const nextRowKey = createEditorRowKeys()
    const draft = draftFromArgument(detail!, nextRowKey)
    expect(draft.targets[0]?.rowKey).toBeTruthy()
    expect(draft.sources[0]?.rowKey).toBeTruthy()
    expect(draft.targets[0]?.rowKey).not.toBe(draft.sources[0]?.rowKey)
    expect(nextRowKey()).not.toBe(draft.sources[0]?.rowKey)
    const otherEditor = createEditorRowKeys()
    expect(otherEditor()).not.toBe(draft.targets[0]?.rowKey)
    draft.targets.push({
      rowKey: 'extra',
      type: 'argument',
      id: 'A9',
      name: 'Other',
      kind: 'argument',
      verdict_id: 'opposes',
    })
    expect(detail?.targets).toHaveLength(1)
    expect(draft.sources[0]?.pages).toBe('2')
    const durable = argumentEditorDraftFromForm(draft)
    expect(durable.targets[0]).toEqual({
      type: 'position',
      id: 'P1',
      name: 'P',
      kind: '',
      verdict_id: 'supports',
    })
    expect(durable.sources[0]).toEqual({ work_id: 'W1', work_title: 'Work', pages: '2' })
    expect(durable.targets.some((row) => 'rowKey' in row)).toBe(false)
    expect(durable.sources.some((row) => 'rowKey' in row)).toBe(false)
  })
})
