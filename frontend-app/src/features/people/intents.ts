import type { InjectionKey } from 'vue'
import type { PersonFieldDraft } from './types'

/** Owning TabContext fields People intents need. Not a second route model. */
export interface PeopleIntentOwner {
  tabId?: string
  generation?: number
  isCurrent?: (generation: number) => boolean
  lastResolvedRoute?: { name?: string; params?: { personId?: string } } | null
  route?: { name?: string; params?: { personId?: string } } | null
  root?: HTMLElement | null
  ui?: {
    personDetailEditing?: boolean
    personEditSession?: number
    personProfileDraft?: {
      personId?: string
      groups?: { id?: string; name?: string }[]
      [key: string]: unknown
    } | null
    personWorksEditing?: boolean
    personOfflineCached?: boolean
  }
  getEntity?: (type: string) => { id?: string; groups?: { id?: string; name?: string }[] } | null
  setEntity?: (type: string, value: unknown) => void
}

export interface PersonSaveResult {
  ok: boolean
  message: string
}

export interface PeopleIntents {
  create(): void
  openPerson(personId: string): void
  beginEdit(personId: string): void
  cancelEdit(personId: string): void
  editSession(): number
  invalidateEditSession(): void
  liveGroupIds(personId: string): string[]
  saveProfile(
    personId: string,
    draft: PersonFieldDraft,
    baseline: PersonFieldDraft,
    groupIds: readonly string[],
    baselineGroupIds: readonly string[],
    session: number,
  ): Promise<PersonSaveResult>
  toggleWorks(personId: string): void
  removeWorkRole(personId: string, workId: string, roleType: string, orderIndex: string): Promise<void>
  remove(personId: string, generation: number): Promise<void>
  viewGraph(): void
  bindDraft(
    personId: string,
    fields: PersonFieldDraft,
    groups: readonly { id: string; name: string }[],
    replaceGroups: boolean,
  ): void
  mountGroups(editor: HTMLElement): void
}

export const peopleIntentsKey: InjectionKey<PeopleIntents> = Symbol('prks-people-intents')

function editSessionOf(owner: PeopleIntentOwner | null | undefined): number {
  const value = owner?.ui?.personEditSession
  return typeof value === 'number' && Number.isFinite(value) ? value : 0
}

function bumpEditSession(owner: PeopleIntentOwner | null | undefined): void {
  if (!owner) return
  if (!owner.ui) owner.ui = {}
  owner.ui.personEditSession = editSessionOf(owner) + 1
}

function repaint(owner: PeopleIntentOwner): void {
  const refresh = window.prksRefreshPersonDetailMain
  if (typeof refresh === 'function') refresh(owner)
}

export function browserPeopleIntents(
  owner: PeopleIntentOwner | null | undefined,
  generation: number,
): PeopleIntents {
  return {
    create() {
      const open = window.openModal
      if (typeof open === 'function') open('person-modal')
    },

    openPerson(personId) {
      const hash = `#/people/${encodeURIComponent(personId)}`
      const navigate = window.prksNavigate
      if (typeof navigate === 'function') navigate(hash, { tabId: owner?.tabId })
    },

    beginEdit(personId) {
      const open = window.openPersonProfileEdit
      if (typeof open === 'function') {
        void open()
        return
      }
      if (!owner?.ui) return
      owner.ui.personDetailEditing = true
      owner.ui.personWorksEditing = false
      void personId
      repaint(owner)
    },

    cancelEdit(personId) {
      bumpEditSession(owner)
      if (owner?.ui) {
        owner.ui.personDetailEditing = false
        owner.ui.personProfileDraft = null
      }
      const close = window.closePersonProfileEdit
      if (typeof close === 'function') close()
      else repaint(owner || {})
      void personId
      void generation
    },

    editSession() {
      return editSessionOf(owner)
    },

    invalidateEditSession() {
      bumpEditSession(owner)
    },

    liveGroupIds(personId) {
      const draft = owner?.ui?.personProfileDraft
      if (!draft || String(draft.personId || '') !== String(personId) || !Array.isArray(draft.groups)) {
        return []
      }
      return draft.groups.map((group) => String(group?.id || '')).filter(Boolean)
    },

    async saveProfile(personId, draft, baseline, groupIds, baselineGroupIds, session) {
      const save = window.savePersonProfileDraft
      if (typeof save !== 'function') return { ok: false, message: 'Could not save this profile.' }
      return save(owner, personId, draft, baseline, groupIds, baselineGroupIds, session)
    },

    toggleWorks() {
      const toggle = window.prksTogglePersonWorksEdit
      if (typeof toggle === 'function') toggle(owner)
    },

    async removeWorkRole(personId, workId, roleType, orderIndex) {
      const remove = window.prksRemoveWorkRoleLink
      if (typeof remove !== 'function') return
      const button = document.createElement('button')
      button.setAttribute('data-person-id', personId)
      button.setAttribute('data-work-id', workId)
      button.setAttribute('data-role-type', roleType)
      button.setAttribute('data-order-index', orderIndex)
      await remove(button)
    },

    async remove(personId, routeGeneration) {
      const deletePerson = window.deletePerson
      if (typeof deletePerson !== 'function') return
      await deletePerson(owner, routeGeneration)
      void personId
    },

    viewGraph() {
      const view = window.prksPersonViewInGraph
      if (typeof view === 'function') view()
    },

    bindDraft(personId, fields, groups, replaceGroups) {
      const bind = window.prksBindPersonProfileDraft
      if (typeof bind === 'function') bind(owner, personId, fields, groups, replaceGroups)
    },

    mountGroups(editor) {
      const mount = window.prksMountPersonProfileGroupPicker
      if (typeof mount !== 'function' || !owner) return
      const person = owner.getEntity ? owner.getEntity('person') : null
      void mount(owner, person, editor)
    },
  }
}
