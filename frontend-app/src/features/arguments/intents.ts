import type { InjectionKey } from 'vue'
import { argumentIndexHash } from './match'
import type {
  ArgumentDetail,
  ArgumentEditorDraft,
  ArgumentEditorSource,
  ArgumentEditorTarget,
  ArgumentKind,
} from './types'

/** Owning TabContext fields Argument intents need. Not a second route model. */
export interface ArgumentIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
  getEntity?: (type: string) => { id?: string } | null
  ui?: { argumentEditing?: boolean }
}

export interface ArgumentPickerItem {
  id: string
  label: string
  kind: string
  pickType: 'position' | 'argument' | 'work'
  haystack: string
}

export interface ArgumentIntents {
  create(kind: ArgumentKind): Promise<void>
  filterKind(kind: 'all' | ArgumentKind): void
  viewGraph(argument: ArgumentDetail): void
  enterEdit(argumentId: string): Promise<boolean>
  cancelEdit(): void
  save(argumentId: string, draft: ArgumentEditorDraft): Promise<boolean>
  createResponse(argument: ArgumentDetail): Promise<void>
  remove(argument: ArgumentDetail): Promise<void>
  pickTarget(
    selfId: string,
    onPick: (target: ArgumentEditorTarget) => void,
  ): Promise<void>
  pickSource(argumentId: string, onPick: (source: ArgumentEditorSource) => void): Promise<void>
}

export const argumentIntentsKey: InjectionKey<ArgumentIntents> = Symbol('prks-argument-intents')

function messageOf(err: unknown): string {
  if (err && typeof err === 'object' && 'message' in err) {
    return String((err as { message?: unknown }).message || '')
  }
  return ''
}

function ownsGeneration(owner: ArgumentIntentOwner | null | undefined, generation: number): boolean {
  return !!(owner && typeof owner.isCurrent === 'function' && owner.isCurrent(generation))
}

function currentRoute(owner: ArgumentIntentOwner | null | undefined): { name?: string } | null {
  return owner?.lastResolvedRoute || owner?.route || null
}

function ownsIndex(owner: ArgumentIntentOwner | null | undefined, generation: number): boolean {
  if (!ownsGeneration(owner, generation)) return false
  return currentRoute(owner)?.name === 'arguments'
}

function ownsDetail(
  owner: ArgumentIntentOwner | null | undefined,
  generation: number,
  argumentId: string,
): boolean {
  const owns = window.prksTabContextOwnsEntityRoute
  if (typeof owns === 'function') {
    return owns(owner, generation, 'argument', argumentId, 'argument-detail')
  }
  if (!ownsGeneration(owner, generation)) return false
  if (currentRoute(owner)?.name !== 'argument-detail') return false
  const live = owner?.getEntity?.('argument')
  return !!(live && String(live.id) === String(argumentId))
}

function setEditingFlag(owner: ArgumentIntentOwner | null | undefined, editing: boolean): void {
  if (owner?.ui) owner.ui.argumentEditing = editing
}

/** Catalogue reads and an open picker can outlive Cancel or a route change. */
function editorStillCurrent(
  owner: ArgumentIntentOwner | null | undefined,
  generation: number,
  argumentId: string,
): boolean {
  return ownsDetail(owner, generation, argumentId) && owner?.ui?.argumentEditing === true
}

async function alertArgument(title: string, err: unknown, fallback: string): Promise<void> {
  const alertFn = window.prksAlertDialog
  if (typeof alertFn !== 'function') return
  const saveMessage = window.prksArgumentSaveMessage
  const message =
    typeof saveMessage === 'function' ? saveMessage(err, fallback) : messageOf(err) || fallback
  await alertFn({ title, message })
}

/**
 * `createArgument` already turns a durable failure into a user-facing Error.
 * Mapping that Error again sees no code and replaces the message with the
 * generic default. Show the message the create API already produced.
 */
async function alertCreateFailure(title: string, err: unknown, fallback: string): Promise<void> {
  const alertFn = window.prksAlertDialog
  if (typeof alertFn !== 'function') return
  await alertFn({ title, message: messageOf(err).trim() || fallback })
}

/** Positions and other Arguments/Stances. The Argument being edited is excluded. */
export function argumentTargetPickerItems(
  args: readonly { id?: string; name?: string; kind?: string }[],
  positions: readonly { id?: string; name?: string }[],
  selfId: string,
): ArgumentPickerItem[] {
  const out: ArgumentPickerItem[] = []
  for (const position of positions) {
    const id = String(position?.id || '')
    if (!id) continue
    out.push({
      id,
      label: position.name || id,
      kind: 'Position',
      pickType: 'position',
      haystack: `${position.name || ''} ${id}`,
    })
  }
  for (const argument of args) {
    const id = String(argument?.id || '')
    if (!id || id === selfId) continue
    const kind = argument.kind === 'stance' ? 'Stance' : 'Argument'
    out.push({
      id,
      label: argument.name || id,
      kind,
      pickType: 'argument',
      haystack: `${argument.name || ''} ${id} ${argument.kind || ''}`,
    })
  }
  return out
}

export function argumentSourcePickerItems(
  works: readonly { id?: string; title?: string }[],
): ArgumentPickerItem[] {
  const out: ArgumentPickerItem[] = []
  for (const work of works) {
    const id = String(work?.id || '')
    if (!id) continue
    out.push({
      id,
      label: work.title || id,
      kind: 'Work',
      pickType: 'work',
      haystack: `${work.title || ''} ${id}`,
    })
  }
  return out
}

/**
 * Typed intents → existing durable Argument APIs and the canonical picker.
 * No durable-queue reads, no fetch, no TanStack mutations, no second queue.
 */
export function browserArgumentIntents(
  owner: ArgumentIntentOwner | null | undefined,
  generation: number,
): ArgumentIntents {
  return {
    async create(kind) {
      const prompt = window.prksPromptTextDialog
      if (typeof prompt !== 'function') return
      const label = kind === 'stance' ? 'Stance' : 'Argument'
      const name = await prompt({
        title: kind === 'stance' ? 'New Stance' : 'New Argument',
        okLabel: 'Create',
      })
      if (name == null || !String(name).trim()) return
      if (!ownsIndex(owner, generation)) return
      let created: { id?: string } | null = null
      try {
        const create = window.createArgument
        if (typeof create !== 'function') throw new Error(`Could not create ${label}.`)
        created = await create({ name: String(name).trim(), kind })
      } catch (err) {
        if (!ownsIndex(owner, generation)) return
        await alertCreateFailure(`Could not create ${label}`, err, `create this ${label}`)
        return
      }
      if (created?.id && ownsIndex(owner, generation) && typeof window.prksNavigate === 'function') {
        window.prksNavigate(`#/arguments/${encodeURIComponent(created.id)}`, { tabId: owner?.tabId })
      }
    },

    filterKind(kind) {
      if (!ownsIndex(owner, generation) || typeof window.prksNavigate !== 'function') return
      window.prksNavigate(argumentIndexHash(kind), { tabId: owner?.tabId })
    },

    viewGraph(argument) {
      if (!ownsDetail(owner, generation, argument.id)) return
      const focusHash = window.prksGraphFocusHash
      const hash =
        typeof focusHash === 'function'
          ? focusHash('argument', argument.id)
          : `#/graph?focus=${encodeURIComponent(`argument:${argument.id}`)}`
      if (typeof window.prksNavigate === 'function') {
        window.prksNavigate(hash, { tabId: owner?.tabId })
      }
    },

    async enterEdit(argumentId) {
      const prepare = window.prksPrepareArgumentEdit
      if (typeof prepare === 'function') await prepare(argumentId)
      if (!ownsDetail(owner, generation, argumentId)) return false
      setEditingFlag(owner, true)
      return true
    },

    cancelEdit() {
      setEditingFlag(owner, false)
    },

    async save(argumentId, draft) {
      try {
        const commit = window.prksCommitArgumentEditorDraft
        if (typeof commit !== 'function') throw new Error('Could not save this Argument.')
        await commit(argumentId, draft)
        if (!ownsDetail(owner, generation, argumentId)) return false
        setEditingFlag(owner, false)
        if (typeof window.prksNavigate === 'function') {
          window.prksNavigate(`#/arguments/${encodeURIComponent(argumentId)}`, {
            replace: true,
            tabId: owner?.tabId,
          })
        }
        return true
      } catch (err) {
        if (!ownsGeneration(owner, generation)) return false
        await alertArgument('Could not save', err, 'save this Argument')
        return false
      }
    },

    async createResponse(argument) {
      const prompt = window.prksPromptTextDialog
      if (typeof prompt !== 'function') return
      const name = await prompt({ title: 'New response argument', okLabel: 'Create' })
      if (name == null || !String(name).trim()) return
      if (!ownsDetail(owner, generation, argument.id)) return
      let created: { id?: string } | null = null
      try {
        const create = window.createArgument
        if (typeof create !== 'function') throw new Error('Could not create response.')
        created = await create({
          name: String(name).trim(),
          kind: 'argument',
          targets: [{ type: 'argument', id: argument.id, verdict_id: 'opposes' }],
        })
      } catch (err) {
        if (!ownsGeneration(owner, generation)) return
        await alertCreateFailure('Could not create response', err, 'create this Argument')
        return
      }
      if (
        created?.id &&
        ownsDetail(owner, generation, argument.id) &&
        typeof window.prksNavigate === 'function'
      ) {
        window.prksNavigate(`#/arguments/${encodeURIComponent(created.id)}`, { tabId: owner?.tabId })
      }
    },

    async remove(argument) {
      const confirmFn = window.prksConfirmDestructive
      const ok =
        typeof confirmFn === 'function'
          ? await confirmFn({
              title: argument.kind === 'stance' ? 'Delete Stance?' : 'Delete Argument?',
              message: 'Remove note references and incoming responses first if deletion is blocked.',
              confirmLabel: argument.kind === 'stance' ? 'Delete Stance' : 'Delete Argument',
            })
          : true
      if (!ok) return
      if (!ownsGeneration(owner, generation)) return
      try {
        const remove = window.prksDeleteArgumentDurably
        if (typeof remove !== 'function') throw new Error('Could not delete Argument.')
        await remove(argument.id)
        if (
          ownsDetail(owner, generation, argument.id) &&
          typeof window.prksNavigate === 'function'
        ) {
          window.prksNavigate('#/arguments', { replace: true, tabId: owner?.tabId })
        }
      } catch (err) {
        if (!ownsGeneration(owner, generation)) return
        const alertFn = window.prksAlertDialog
        if (typeof alertFn === 'function') {
          await alertFn({
            title: 'Cannot delete',
            message: messageOf(err) || 'Could not delete Argument.',
          })
        }
      }
    },

    async pickTarget(selfId, onPick) {
      const open = window.prksOpenResearchPicker
      if (typeof open !== 'function') return
      if (!editorStillCurrent(owner, generation, selfId)) return
      const argsPromise =
        typeof window.fetchArguments === 'function' ? window.fetchArguments() : Promise.resolve([])
      const positionsPromise =
        typeof window.fetchPositions === 'function' ? window.fetchPositions() : Promise.resolve([])
      const [args, positions] = await Promise.all([argsPromise, positionsPromise])
      if (!editorStillCurrent(owner, generation, selfId)) return
      const items = argumentTargetPickerItems(args || [], positions || [], selfId)
      open({
        title: 'Responds to',
        items: () => items,
        onPick: (id, pickType) => {
          if (!editorStillCurrent(owner, generation, selfId)) return
          const chosen = items.find((item) => item.id === id)
          onPick({
            type: pickType === 'argument' ? 'argument' : 'position',
            id,
            name: chosen?.label || id,
            kind: chosen?.kind === 'Stance' ? 'stance' : chosen?.kind === 'Argument' ? 'argument' : '',
            verdict_id: '',
          })
        },
      })
    },

    async pickSource(argumentId, onPick) {
      const open = window.prksOpenResearchPicker
      if (typeof open !== 'function') return
      if (!editorStillCurrent(owner, generation, argumentId)) return
      const works = typeof window.fetchWorks === 'function' ? await window.fetchWorks() : []
      if (!editorStillCurrent(owner, generation, argumentId)) return
      const items = argumentSourcePickerItems(works || [])
      open({
        title: 'Source work',
        items: () => items,
        onPick: (id) => {
          if (!editorStillCurrent(owner, generation, argumentId)) return
          const chosen = items.find((item) => item.id === id)
          onPick({
            work_id: id,
            work_title: chosen?.label || id,
            pages: '',
          })
        },
      })
    },
  }
}
