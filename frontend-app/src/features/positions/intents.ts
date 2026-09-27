import type { InjectionKey } from 'vue'
import type { PositionDetail } from './types'

/** Owning TabContext fields Position intents need. Not a second route model. */
export interface PositionIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string } | null
  route?: { name?: string } | null
}

export interface PositionIntents {
  create(): Promise<void>
  viewGraph(position: PositionDetail): void
}

export const positionIntentsKey: InjectionKey<PositionIntents> = Symbol('prks-position-intents')

function ownsIndex(owner: PositionIntentOwner | null | undefined, generation: number): boolean {
  if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return false
  const route = owner.lastResolvedRoute || owner.route
  return !!(route && route.name === 'positions')
}

/**
 * Typed intents → existing durable Position APIs and navigation helpers.
 * No network reads, no TanStack mutations, no second durable queue.
 */
export function browserPositionIntents(
  owner: PositionIntentOwner | null | undefined,
  generation: number,
): PositionIntents {
  return {
    async create() {
      const prompt = window.prksPromptTextDialog
      if (typeof prompt !== 'function') return
      const name = await prompt({
        title: 'New Position',
        okLabel: 'Create',
      })
      if (!name || !String(name).trim()) return
      if (!ownsIndex(owner, generation)) return
      let created: { id?: string } | null = null
      try {
        const create = window.createPosition
        if (typeof create !== 'function') {
          throw new Error('Could not create Position.')
        }
        created = await create({ name: String(name).trim() })
      } catch (err) {
        if (!ownsIndex(owner, generation)) return
        const alertFn = window.prksAlertDialog
        if (typeof alertFn === 'function') {
          await alertFn({
            title: 'Could not create Position',
            message:
              (err && typeof err === 'object' && 'message' in err
                ? String((err as { message?: unknown }).message || '')
                : '') || 'Could not create Position.',
          })
        }
        return
      }
      if (created && created.id && ownsIndex(owner, generation) && typeof window.prksNavigate === 'function') {
        window.prksNavigate(`#/positions/${encodeURIComponent(created.id)}`, {
          tabId: owner?.tabId,
        })
      }
    },

    viewGraph(position) {
      if (!owner || typeof owner.isCurrent !== 'function' || !owner.isCurrent(generation)) return
      const focusHash = window.prksGraphFocusHash
      const hash =
        typeof focusHash === 'function'
          ? focusHash('position', position.id)
          : `#/graph?focus=${encodeURIComponent(`position:${position.id}`)}`
      if (typeof window.prksNavigate === 'function') {
        window.prksNavigate(hash, { tabId: owner.tabId })
      }
    },
  }
}
